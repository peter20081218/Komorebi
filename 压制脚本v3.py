#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import subprocess
import sys
import os
import re
import time
import json
from pathlib import Path

# 强制系统输出采用 UTF-8 编码，彻底解决中文字符输出乱码问题
sys.stdout.reconfigure(encoding='utf-8')
if os.name == 'nt':
    os.system('chcp 65001 > nul 2>&1')

class FFmpegOrchestrator:
    def __init__(self):
        self.ffmpeg_cmd = "ffmpeg"
        self.ffprobe_cmd = "ffprobe"
        self.check_ffmpeg_installed()
        self.available_hw_encoders = self.probe_hardware_encoders()

    def print_guide(self, text, message_type="INFO"):
        """格式化全中文引导输出 (绝对安全语法版本)"""
        prefixes = dict()
        prefixes.update({"INFO": "(系统提示) "})
        prefixes.update({"SUCCESS": "(成功) "})
        prefixes.update({"WARNING": "(警告) "})
        prefixes.update({"ERROR": "(错误) "})
        print(prefixes.get(message_type, "") + text)

    def check_ffmpeg_installed(self):
        try:
            cmd_tuple = tuple((self.ffmpeg_cmd, "-version"))
            subprocess.run(cmd_tuple, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
        except FileNotFoundError:
            self.print_guide("未在系统中找到 FFmpeg 核心组件！请确保已安装并配置了环境变量。", "ERROR")
            sys.exit(1)

    def probe_hardware_encoders(self):
        """探测当前显卡支持的硬件编码器加速接口"""
        encoders = list()
        try:
            cmd_tuple = tuple((self.ffmpeg_cmd, "-encoders"))
            result = subprocess.run(cmd_tuple, capture_output=True, text=True, encoding='utf-8', shell=False)
            output = result.stdout
            if "hevc_nvenc" in output or "h264_nvenc" in output:
                encoders.append("nvenc") 
            if "av1_nvenc" in output:
                encoders.append("av1_nvenc")
            if "hevc_qsv" in output or "h264_qsv" in output:
                encoders.append("qsv")
            if "av1_qsv" in output:
                encoders.append("av1_qsv")
            if "hevc_amf" in output or "h264_amf" in output:
                encoders.append("amf")
            if "av1_amf" in output:
                encoders.append("av1_amf")
        except Exception:
            pass
        return encoders

    def check_media_info(self, filepath):
        """利用 ffprobe 智能探测视频源文件属性，返回体积、时长、分辨率、视音频编码格式字典"""
        info = dict()
        try:
            size_mb = os.path.getsize(filepath) / (1024 * 1024)
            info.update({"size_mb": size_mb})
            
            # 使用 JSON 格式进行深层探测，提取时长和分辨率
            cmd_probe = tuple((self.ffprobe_cmd, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", filepath))
            res_probe = subprocess.run(cmd_probe, capture_output=True, text=True, encoding='utf-8', shell=False)
            probe_data = json.loads(res_probe.stdout)
            
            duration = float(probe_data.get("format", dict()).get("duration", 0))
            info.update({"duration": duration})
            
            v_codec = "无"
            a_codec = "无"
            width = 1920
            height = 1080
            
            for stream in probe_data.get("streams", list()):
                if stream.get("codec_type") == "video" and v_codec == "无":
                    v_codec = stream.get("codec_name", "未知")
                    width = int(stream.get("width", 1920))
                    height = int(stream.get("height", 1080))
                elif stream.get("codec_type") == "audio" and a_codec == "无":
                    a_codec = stream.get("codec_name", "未知")
                    
            info.update({"v_codec": v_codec, "a_codec": a_codec, "width": width, "height": height})
            
        except Exception:
            info.update({"size_mb": 0.0, "duration": 0.0, "v_codec": "未知", "a_codec": "未知", "width": 1920, "height": 1080})
        return info

    def validate_and_create_path(self, user_path, is_dir=False):
        path_obj = Path(user_path).resolve()
        if is_dir:
            path_obj.mkdir(parents=True, exist_ok=True)
        return str(path_obj)

    def _estimate_size_and_loss(self, duration, width, height, target_codec, level):
        """全新的精确体积与画质预测算法（基于分辨率、时长与基准码率）"""
        if duration <= 0:
            return "未知", "未知"
            
        # 根据分辨率计算缩放因子 (基准为 1080p)
        pixel_count = width * height
        base_pixels = 1920 * 1080
        res_factor = pixel_count / base_pixels if pixel_count > 0 else 1.0
        
        # 定义 HEVC 在 1080p 下四个挡位的推荐基准视频码率 (kbps)
        base_hevc_kbps = dict()
        base_hevc_kbps.update({"1": 3500, "2": 1500, "3": 800, "4": 400})
        
        # 编码器效率乘数因子
        codec_multiplier = dict()
        codec_multiplier.update({"h264": 1.5, "hevc": 1.0, "av1": 0.75})
        
        target_video_kbps = base_hevc_kbps.get(level, 1500) * codec_multiplier.get(target_codec, 1.0) * res_factor
        target_audio_kbps = 128 # 预估默认音频码率
        total_kbps = target_video_kbps + target_audio_kbps
        
        # 计算最终预估体积 MB = (kbps * 秒) / 8192
        est_mb = (total_kbps * duration) / 8192.0
        
        loss_eval = dict()
        loss_eval.update({"1": "<1% (无损级)", "2": "~5% (极低损)", "3": "~15% (明显压缩)", "4": "~30% (画质劣化)"})
        
        return str(round(est_mb, 1)), loss_eval.get(level, "未知")

    def build_video_compress_command(self, input_file, output_file, video_category, quality_level, target_vcodec, use_hw=True, ext_audio=None):
        """构建全场景差异化高保真画质与压缩的独立算法参数链"""
        cmd = list()
        
        # 1. 外部音轨合并与映射逻辑
        if ext_audio and ext_audio!= "none":
            cmd.extend((self.ffmpeg_cmd, "-y", "-i", input_file, "-i", ext_audio))
            cmd.extend(("-map", "0:v", "-map", "1:a"))
            cmd.extend(("-c:a", "libopus" if target_vcodec == "av1" else "aac", "-b:a", "192k"))
        elif ext_audio == "none":
            cmd.extend((self.ffmpeg_cmd, "-y", "-i", input_file))
            cmd.extend(("-map", "0:v"))
        else:
            cmd.extend((self.ffmpeg_cmd, "-y", "-i", input_file))
            cmd.extend(("-map", "0"))
            cmd.extend(("-c:a", "libopus" if target_vcodec == "av1" else "aac", "-b:a", "192k", "-c:s", "copy"))

        # 2. 获取预设挡位对应的 CRF
        level_crf_map = dict()
        level_crf_map.update({"1": "19", "2": "23", "3": "27", "4": "31"})
        cq_val = level_crf_map.get(quality_level, "23")
        
        x265_params = ""
        svtav1_params = ""
        hw_preset = "p6"
        
        # 3. 三大场景独立算法预设
        if video_category == "1": # 动漫
            if quality_level in ("1", "2"):
                x265_params = "limit-sao=1:bframes=8:psy-rd=1.0:aq-mode=3:aq-strength=0.8:deblock=0,0"
                svtav1_params = "tune=0:enable-overlays=1:scd=1"
                hw_preset = "p6"
            else:
                x265_params = "limit-sao=1:bframes=8:psy-rd=0.6:aq-mode=3:deblock=1,1:qcomp=0.6"
                svtav1_params = "tune=0:enable-overlays=1:scd=1"
                hw_preset = "p4"

        elif video_category == "2": # 录屏
            x265_params = "strong-intra-smoothing=0:rect=0:aq-mode=1:deblock=-1,-1:bframes=8:keyint=300"
            svtav1_params = "tune=0:enable-overlays=1:scd=1:scm=2"
            hw_preset = "p5"
            
        else: # 实拍
            if quality_level in ("1", "2"):
                x265_params = "no-sao=1:bframes=4:psy-rd=1.5:psy-rdoq=2.0:aq-mode=2"
                svtav1_params = "tune=2:film-grain=8"
                hw_preset = "p7"
            else:
                x265_params = "limit-sao=1:bframes=4:psy-rd=1.0:aq-mode=2"
                svtav1_params = "tune=2:film-grain=4"
                hw_preset = "p5"

        # 4. 编码器硬件自适应引擎
        if use_hw and len(self.available_hw_encoders) > 0:
            if "nvenc" in self.available_hw_encoders:
                if target_vcodec == "av1" and "av1_nvenc" in self.available_hw_encoders:
                    encoder = "av1_nvenc"
                else:
                    encoder = "hevc_nvenc" if target_vcodec == "hevc" else "h264_nvenc"
                
                hw_args = list()
                # NVENC 必须使用合法 preset (p1-p7) 和 tune (hq, ll, ull, lossless)
                hw_args.extend(("-c:v", encoder, "-preset", hw_preset, "-rc", "vbr", "-cq", cq_val))
                if video_category == "1":
                    hw_args.extend(("-spatial-aq", "1", "-tune", "hq", "-bf", "3"))
                elif video_category == "2":
                    hw_args.extend(("-spatial-aq", "1", "-tune", "hq", "-bf", "4", "-g", "300"))
                else:
                    hw_args.extend(("-spatial-aq", "1", "-temporal-aq", "1", "-tune", "hq", "-bf", "3"))
                cmd.extend(hw_args)

            elif "qsv" in self.available_hw_encoders:
                if target_vcodec == "av1" and "av1_qsv" in self.available_hw_encoders:
                    encoder = "av1_qsv"
                else:
                    encoder = "hevc_qsv" if target_vcodec == "hevc" else "h264_qsv"
                cmd.extend(("-c:v", encoder, "-preset", "slower", "-global_quality", cq_val, "-look_ahead", "1")) 
            elif "amf" in self.available_hw_encoders:
                if target_vcodec == "av1" and "av1_amf" in self.available_hw_encoders:
                    encoder = "av1_amf"
                else:
                    encoder = "hevc_amf" if target_vcodec == "hevc" else "h264_amf"
                cmd.extend(("-c:v", encoder, "-rc", "cqp", "-qp_i", cq_val, "-qp_p", cq_val, "-qp_b", cq_val)) 
        else:
            # 纯 CPU 软件算法矩阵
            if target_vcodec == "av1":
                cmd.extend(("-c:v", "libsvtav1", "-preset", "6", "-crf", cq_val, "-svtav1-params", svtav1_params))
            elif target_vcodec == "hevc":
                cpu_args = list()
                cpu_args.extend(("-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-preset", "slow", "-crf", cq_val))
                if video_category == "1":
                    cpu_args.extend(("-tune", "animation"))
                cpu_args.extend(("-x265-params", x265_params))
                cmd.extend(cpu_args)
            else:
                tune_arg = "animation" if video_category == "1" else "film"
                cmd.extend(("-c:v", "libx264", "-preset", "slow", "-crf", cq_val, "-tune", tune_arg))
        
        # 保护防崩机制：如果目标是 H.264，不论软硬解都强制降级为 8-bit 色深，防止 10-bit 源导致硬件直接崩溃
        if target_vcodec == "h264":
            cmd.extend(("-pix_fmt", "yuv420p"))

        cmd.append(output_file)
        return cmd

    def build_format_factory_command(self, input_file, output_file, format_type, compress_level=None, use_hw=True, target_vcodec="h264", ext_audio=None):
        """构建全生态格式化工厂，支持音视频与动图转换，并复用外部音轨逻辑"""
        cmd = list()
        
        # 1. 外部音轨合并处理 (在格式化工厂同样适用)
        if ext_audio and ext_audio!= "none":
            cmd.extend((self.ffmpeg_cmd, "-y", "-i", input_file, "-i", ext_audio))
            if format_type.lower() not in ("mp3", "flac", "wav", "aac", "ogg", "wma", "opus", "gif", "webp"):
                cmd.extend(("-map", "0:v", "-map", "1:a"))
        elif ext_audio == "none":
            cmd.extend((self.ffmpeg_cmd, "-y", "-i", input_file))
            if format_type.lower() not in ("mp3", "flac", "wav", "aac", "ogg", "wma", "opus", "gif", "webp"):
                cmd.extend(("-map", "0:v"))
        else:
            cmd.extend((self.ffmpeg_cmd, "-y", "-i", input_file))
            if format_type.lower() not in ("mp3", "flac", "wav", "aac", "ogg", "wma", "opus", "gif", "webp"):
                cmd.extend(("-map", "0"))

        format_type = format_type.lower()
        audio_formats = ("mp3", "flac", "wav", "m4a", "aac", "ogg", "wma", "opus", "ac3")
        image_formats = ("gif", "webp")
        
        if format_type in audio_formats:
            cmd.append("-vn") # 剔除视频轨
            if compress_level:
                audio_kbps = dict()
                audio_kbps.update({"1": "320k", "2": "192k", "3": "128k", "4": "64k"})
                target_br = audio_kbps.get(compress_level, "192k")
                
                if format_type == "mp3":
                    cmd.extend(("-c:a", "libmp3lame", "-b:a", target_br)) 
                elif format_type in ("flac", "wav"):
                    self.print_guide("注意: FLAC与WAV为无损音频格式，已拦截压缩请求，将原尺寸无损导出。", "WARNING")
                    cmd.extend(("-c:a", "flac" if format_type == "flac" else "pcm_s16le"))
                elif format_type == "opus":
                    cmd.extend(("-c:a", "libopus", "-b:a", target_br, "-vbr", "on"))
                else:
                    cmd.extend(("-c:a", "aac", "-b:a", target_br))
            else:
                cmd.extend(("-c:a", "copy"))
                
        elif format_type in image_formats:
            self.print_guide("加载高画质动图生成算法...", "INFO")
            if format_type == "gif":
                gif_filter = "fps=15,scale=480:-1:flags=lanczos,splitLs0RLs1R;Ls0RpalettegenLpR;Ls1RLpRpaletteuse=dither=sierra2_4a".replace("L", chr(91)).replace("R", chr(93))
                cmd.extend(("-vf", gif_filter, "-loop", "0"))
            else:
                cmd.extend(("-vcodec", "libwebp", "-lossless", "0", "-qscale", "75", "-preset", "default", "-loop", "0", "-an"))

        elif format_type == "3gp":
            cmd.extend(("-c:v", "h263", "-s", "352x288", "-r", "15", "-c:a", "libopencore_amrnb", "-ar", "8000", "-ac", "1"))
            
        else: # 常规视频互转
            if compress_level:
                level_crf_map = dict()
                level_crf_map.update({"1": "19", "2": "23", "3": "27", "4": "31"})
                cq_val = level_crf_map.get(compress_level, "23")
                
                if use_hw and len(self.available_hw_encoders) > 0:
                    if "nvenc" in self.available_hw_encoders:
                        encoder = "av1_nvenc" if target_vcodec == "av1" else ("hevc_nvenc" if target_vcodec == "hevc" else "h264_nvenc")
                        cmd.extend(("-c:v", encoder, "-preset", "p5", "-rc", "vbr", "-cq", cq_val))
                    elif "qsv" in self.available_hw_encoders:
                        encoder = "av1_qsv" if target_vcodec == "av1" else ("hevc_qsv" if target_vcodec == "hevc" else "h264_qsv")
                        cmd.extend(("-c:v", encoder, "-preset", "medium", "-global_quality", cq_val))
                    elif "amf" in self.available_hw_encoders:
                        encoder = "av1_amf" if target_vcodec == "av1" else ("hevc_amf" if target_vcodec == "hevc" else "h264_amf")
                        cmd.extend(("-c:v", encoder, "-rc", "cqp", "-qp_i", cq_val, "-qp_p", cq_val, "-qp_b", cq_val))
                else:
                    if target_vcodec == "av1":
                        cmd.extend(("-c:v", "libsvtav1", "-preset", "8", "-crf", cq_val))
                    else:
                        encoder = "libx265" if target_vcodec == "hevc" else "libx264"
                        cmd.extend(("-c:v", encoder, "-crf", cq_val, "-preset", "medium"))
                
                if target_vcodec == "h264":
                    cmd.extend(("-pix_fmt", "yuv420p"))

                if not ext_audio and ext_audio!= "none":
                    cmd.extend(("-c:a", "libopus" if target_vcodec == "av1" else "aac", "-b:a", "128k"))
            else:
                cmd.extend(("-c:v", "copy"))
                if not ext_audio and ext_audio!= "none":
                    cmd.extend(("-c:a", "copy"))

        cmd.append(output_file)
        return cmd

    def diagnostic_error_translator(self, error_log):
        """精准故障分析字典，分离已知错误与未知崩溃"""
        if "No such file or directory" in error_log:
            return "【路径解析错误】找不到输入文件。请检查路径中是否包含系统无法识别的罕见特殊符号，或尝试将文件直接移至磁盘根目录重试。", True
        if "10 bit encode not supported" in error_log:
            return "【硬件支持受限】您的显卡引擎过于老旧，不支持10位色深的视频压制（常见于较老的H.264显卡硬解），系统内部回退保护可能未成功拦截。", True
        if "Error while opening encoder" in error_log or "No capable devices found" in error_log:
            return "【编码器初始化失败】当前显卡不支持您所选的编码格式（如较老的显卡不支持HEVC B帧或AV1），或显卡驱动需要更新。", True
        if "AVERROR_INVALIDDATA" in error_log or "Invalid data found" in error_log:
            return "【数据损毁】源视频数据无效或破损。该视频极有可能未下载完整，或者被其他软件锁死了写入权限。", True
        if "Out of memory" in error_log or "cannot allocate memory" in error_log:
            return "【系统资源耗尽】当前计算机内存或显存已满。请关闭其他大型软件后再进行转码。", True
        return "发生了系统无法判定归类的深层级异常崩溃。", False

    def execute_with_fallback(self, cmd, is_video_compress=False, input_file="", output_file="", out_dir="", **kwargs):
        self.print_guide("\n===========================================", "INFO")
        cmd_str = " ".join(cmd)
        self.print_guide("正在执行任务...\n底层调用链: " + cmd_str, "INFO")
        
        process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore', shell=False, universal_newlines=True)
        
        error_buffer = ""
        duration_secs = 0
        start_time = time.time()
        
        for line in process.stderr:
            error_buffer += line
            
            if duration_secs == 0:
                match_duration = re.search(r"Duration:\s*(\d{2}):(\d{2}):(\d{2}\.\d+)", line)
                if match_duration:
                    duration_secs = int(match_duration.group(1)) * 3600 + int(match_duration.group(2)) * 60 + float(match_duration.group(3))
            
            if duration_secs > 0:
                match_time = re.search(r"time=\s*(\d{2}):(\d{2}):(\d{2}\.\d+)", line)
                if match_time:
                    current_secs = int(match_time.group(1)) * 3600 + int(match_time.group(2)) * 60 + float(match_time.group(3))
                    percent = min((current_secs / duration_secs) * 100, 100.0)
                    
                    elapsed = time.time() - start_time
                    if elapsed > 0 and current_secs > 0:
                        speed = current_secs / elapsed
                        eta = (duration_secs - current_secs) / speed
                        if eta < 0: eta = 0
                        eta_mins = int(eta // 60)
                        eta_secs = int(eta % 60)
                        eta_str = str(eta_mins) + "分" + str(eta_secs) + "秒"
                    else:
                        eta_str = "计算中..."
                        
                    bar_len = 30
                    filled_len = int(bar_len * percent // 100)
                    bar = '█' * filled_len + '-' * (bar_len - filled_len)
                    sys.stdout.write("\r(系统提示) 处理进度: (" + bar + ") " + str(round(percent, 1)) + "% | 剩余时间约: " + eta_str + "   ")
                    sys.stdout.flush()
                
        process.wait()
        print("") 
        
        exit_code = process.returncode
        if exit_code!= 0:
            self.print_guide("\n任务执行中断！正在分析底层故障原因...", "ERROR")
            diagnostic_msg, is_known_error = self.diagnostic_error_translator(error_buffer)
            self.print_guide("(故障诊断) " + diagnostic_msg, "WARNING")
            
            # --- 日志自动生成系统 (针对无法判断的异常) ---
            if not is_known_error:
                log_file = os.path.join(out_dir, "ffmpeg_error_log_" + str(int(time.time())) + ".txt")
                try:
                    with open(log_file, "w", encoding="utf-8") as f:
                        f.write("========== FFmpeg Crash Log ==========\n")
                        f.write("Command:\n" + cmd_str + "\n\n")
                        f.write("System stderr Output:\n" + error_buffer)
                    self.print_guide("由于无法精准判明原因，系统已将完整的底层报错代码打包生成为日志文件！", "INFO")
                    self.print_guide("日志文件路径: " + log_file, "INFO")
                    self.print_guide("请将此日志文件发送给懂技术的朋友，或自行查看以寻找线索。", "INFO")
                except Exception:
                    self.print_guide("日志文件写入失败，请检查输出目录权限。", "ERROR")

            # --- 硬件崩溃自适应降级 ---
            hw_error_triggers = tuple(("10 bit encode not supported", "No capable devices found", "Error while opening encoder", "AVERROR"))
            is_hw_failure = False
            for trigger in hw_error_triggers:
                if trigger in error_buffer:
                    is_hw_failure = True
                    break
            
            if is_video_compress and is_hw_failure and "libx265" not in cmd and "libx264" not in cmd and "libsvtav1" not in cmd:
                self.print_guide("\n触发容灾机制：检测到显卡加速接口瘫痪，正在自动热切换为纯CPU软压制重试！", "WARNING")
                fallback_cmd = self.build_video_compress_command(
                    input_file, output_file, 
                    video_category=kwargs.get("video_category", "1"),
                    quality_level=kwargs.get("quality_level", "2"), 
                    target_vcodec=kwargs.get("target_vcodec", "hevc"),
                    use_hw=False, 
                    ext_audio=kwargs.get("ext_audio")
                )
                return self.execute_with_fallback(fallback_cmd, is_video_compress=False, input_file=input_file, output_file=output_file, out_dir=out_dir) 
            else:
                self.print_guide("任务不可挽回地失败。请参考上述排障指南或日志文件处理。", "ERROR")
                return False
        else:
            self.print_guide("\n处理成功，文件已保存至目标目录。", "SUCCESS")
            return True

    def _prompt_audio_injection(self, media_info):
        """通用音轨注入向导"""
        has_audio = media_info.get("a_codec")!= "无"
        ext_audio = None
        if not has_audio:
            self.print_guide("【侦测警报】原视频文件中不存在声音/音频轨道！", "WARNING")
            add_audio = input("是否需要混入外部音轨？(y: 合并外部音频 / n: 保持无声纯画面, 默认n): ").strip().lower()
            if add_audio == 'y':
                ext_audio = input("请拖入或粘贴外部音频文件路径: ").strip().strip('"').strip("'")
                ext_audio = self.validate_and_create_path(ext_audio)
            else:
                ext_audio = "none"
        return ext_audio

    def run_interactive_ui(self):
        while True:
            self.print_guide("\n===========================================")
            self.print_guide("  全场景音视频压制与多格式格式化工厂中枢  ")
            self.print_guide("===========================================")
            print("请选择操作模式：\n  1. 场景化视频极致压缩 (针对动漫/录屏/拍摄独立定制算法)\n  2. 全能格式化工厂 (支持全视频/音频互转与自定义压缩)")
            
            choice = input("\n请输入选项数字 (1/2): ").strip()
            
            input_path = input("\n请拖入或粘贴需要处理的文件路径 (完美支持中文): ").strip().strip('"').strip("'")
            input_path = self.validate_and_create_path(input_path)
            
            media_info = self.check_media_info(input_path)
            orig_mb = media_info.get("size_mb", 0)
            orig_duration = media_info.get("duration", 0)
            orig_w = media_info.get("width", 1920)
            orig_h = media_info.get("height", 1080)
            
            print("\n-------------------------------------------")
            self.print_guide("源文件侦测: 时长 " + str(round(orig_duration, 1)) + "秒 | 体积 " + str(round(orig_mb, 2)) + " MB | 分辨率: " + str(orig_w) + "x" + str(orig_h), "INFO")
            self.print_guide("编码状态: [视频] " + media_info.get("v_codec").upper() + " | [音频] " + media_info.get("a_codec").upper(), "INFO")
            
            supported_codecs = ["H.264 (AVC)"]
            if len(self.available_hw_encoders) > 0:
                self.print_guide("检测到系统搭载硬件加速引擎: " + ", ".join(self.available_hw_encoders).upper(), "SUCCESS")
                if any("hevc" in enc for enc in self.available_hw_encoders):
                    supported_codecs.append("H.265 (HEVC)")
                if any("av1" in enc for enc in self.available_hw_encoders):
                    supported_codecs.append("AV1 (AOM)")
            else:
                self.print_guide("系统未检测到可用GPU编码器，将为您调度高画质纯软件算法引擎。", "WARNING")
                supported_codecs.extend(["H.265 (HEVC)", "AV1 (AOM)"])
            print("-------------------------------------------")
            
            out_dir = input("\n请输入自定义的输出文件夹路径 (直接回车保存在源文件同级目录): ").strip().strip('"').strip("'")
            if not out_dir:
                out_dir = str(Path(input_path).parent)
            else:
                out_dir = self.validate_and_create_path(out_dir, is_dir=True)
                
            custom_name = input("请输入自定义输出文件名(不含后缀, 直接回车使用原名加后缀): ").strip()
            base_name = custom_name if custom_name else Path(input_path).stem

            success = False
            output_path = ""
            ext_audio = None
            
            if choice == "1":
                print("\n请选择您要压制的视频场景 (系统将套用各场景专属优化算法)：")
                print("  1. 动漫 / 二次元 (专属去色带与线条锐利保护)")
                print("  2. 电脑录屏 / 游戏 (极高压缩率，保护文字界面不发虚)")
                print("  3. 实拍视频 / 电影 (保留真实噪点，优化复杂物理光影动态)")
                video_category = input("请输入类型数字 (1-3, 默认1): ").strip()
                if video_category not in ("1", "2", "3"):
                    video_category = "1"

                print("\n请选择您的目标视频编码内核：")
                print("  1. H.264 (AVC)  - 兼容性极佳，任何老旧设备均可直接播放")
                print("  2. H.265 (HEVC) - 主流高质量，同画质下体积比H.264小约40%")
                print("  3. AV1 (AOM)    - 次世代免专利费格式，极限微小体积，流媒体未来")
                codec_opt = input("请输入选项数字 (1-3, 默认2): ").strip()
                target_vcodec = "h264"
                if codec_opt == "2": target_vcodec = "hevc"
                if codec_opt == "3": target_vcodec = "av1"

                target_fmt = input("\n请输入输出容器后缀 (如 mp4, mkv, webm 等, 默认 mkv): ").strip().lower()
                if not target_fmt:
                    target_fmt = "mkv"
                    
                if target_fmt == "webm" and target_vcodec in ("h264", "hevc"):
                    self.print_guide("【兼容性保护】WebM 容器规范严格禁止H.264/HEVC，已自动为您将内核切换为开源的 AV1。", "WARNING")
                    target_vcodec = "av1"
                
                print("\n请选择该场景下的压缩预设挡位 (基于时长与分辨率为您精确估算)：")
                s1, l1 = self._estimate_size_and_loss(orig_duration, orig_w, orig_h, target_vcodec, "1")
                s2, l2 = self._estimate_size_and_loss(orig_duration, orig_w, orig_h, target_vcodec, "2")
                s3, l3 = self._estimate_size_and_loss(orig_duration, orig_w, orig_h, target_vcodec, "3")
                s4, l4 = self._estimate_size_and_loss(orig_duration, orig_w, orig_h, target_vcodec, "4")
                print("  1. 极致高保真 (预计画质损失: " + l1 + " | 预估转出: ~" + s1 + " MB)")
                print("  2. 优质归档   (预计画质损失: " + l2 + " | 预估转出: ~" + s2 + " MB)")
                print("  3. 日常流媒体 (预计画质损失: " + l3 + " | 预估转出: ~" + s3 + " MB)")
                print("  4. 极限存储   (预计画质损失: " + l4 + " | 预估转出: ~" + s4 + " MB)")
                quality_level = input("请输入挡位数字 (1-4, 默认2): ").strip()
                if quality_level not in ("1", "2", "3", "4"):
                    quality_level = "2"
                
                # 音轨拦截与注入系统
                ext_audio = self._prompt_audio_injection(media_info)

                output_path = str(Path(out_dir) / (base_name + "_compressed." + target_fmt))
                cmd = self.build_video_compress_command(input_path, output_path, video_category=video_category, quality_level=quality_level, target_vcodec=target_vcodec, use_hw=True, ext_audio=ext_audio)
                
                success = self.execute_with_fallback(
                    cmd, 
                    is_video_compress=True, 
                    input_file=input_path, 
                    output_file=output_path, 
                    out_dir=out_dir,
                    video_category=video_category, 
                    quality_level=quality_level, 
                    target_vcodec=target_vcodec,
                    ext_audio=ext_audio
                )
                
            elif choice == "2":
                print("\n【支持的视频格式】: mp4, mkv, avi, mov, flv, wwmv, webm, 3gp, ts 等")
                print("【支持的动图格式】: gif, webp")
                print("【支持的音频格式】: mp3, flac, wav, aac, m4a, ogg, wma, opus, ac3 等")
                target_fmt = input("请输入您想要转换的最终格式后缀: ").strip().lower()
                output_path = str(Path(out_dir) / (base_name + "_converted." + target_fmt))
                
                compress_level = None
                target_vcodec = "h264"
                audio_formats = ("mp3", "flac", "wav", "m4a", "aac", "ogg", "wma", "opus", "ac3")
                image_formats = ("gif", "webp")
                
                if target_fmt in audio_formats:
                    print("\n是否对音频启用高级压缩引擎？")
                    print("  1. 极高音质归档 (320kbps)")
                    print("  2. 高保真音乐   (192kbps)")
                    print("  3. 平衡流媒体   (128kbps)")
                    print("  4. 极致语音压缩 (64kbps)")
                    print("  n. 原音轨极速直通提取 (不改变任何参数)")
                    opt = input("请输入选项 (1-4 / n, 默认 n): ").strip().lower()
                    if opt in ("1", "2", "3", "4"):
                        compress_level = opt
                elif target_fmt not in image_formats:
                    # 格式化工厂的音轨拦截
                    ext_audio = self._prompt_audio_injection(media_info)
                    
                    print("\n是否在视频转换的同时进行瘦身压缩？")
                    print("  1. 极高画质 (轻度压制)")
                    print("  2. 标准画质 (均衡压制)")
                    print("  3. 较低画质 (强力压制)")
                    print("  4. 极低画质 (极限压制)")
                    print("  n. 仅转换外壳格式 (极速无损 Direct Stream Copy)")
                    opt = input("请输入选项 (1-4 / n, 默认 n): ").strip().lower()
                    
                    if opt in ("1", "2", "3", "4"):
                        compress_level = opt
                        print("\n请选择该格式承载的视频内核：")
                        print("  1. H.264 (兼容性之王, 默认)")
                        print("  2. H.265 (超高性价比)")
                        print("  3. AV1   (次世代极限压缩)")
                        c_opt = input("请输入 (1-3, 默认 1): ").strip()
                        if c_opt == "2": target_vcodec = "hevc"
                        elif c_opt == "3": target_vcodec = "av1"
                        
                        if target_fmt == "webm" and target_vcodec in ("h264", "hevc"):
                            self.print_guide("【兼容性保护】WebM 容器规范严格禁止H.264/HEVC，已自动为您将内核切换为 AV1。", "WARNING")
                            target_vcodec = "av1"
                            
                        s_est, l_est = self._estimate_size_and_loss(orig_duration, orig_w, orig_h, target_vcodec, compress_level)
                        self.print_guide("估算结果: 预计画质损失 " + l_est + " ，输出体积约 " + s_est + " MB。", "INFO")

                cmd = self.build_format_factory_command(input_file, output_path, target_fmt, compress_level=compress_level, use_hw=True, target_vcodec=target_vcodec, ext_audio=ext_audio)
                success = self.execute_with_fallback(cmd, is_video_compress=False, input_file=input_path, output_file=output_path, out_dir=out_dir)
            else:
                self.print_guide("输入错误模式，任务取消。", "ERROR")
                
            if success and os.path.exists(output_path):
                try:
                    orig_size = os.path.getsize(input_path) / (1024 * 1024)
                    new_size = os.path.getsize(output_path) / (1024 * 1024)
                    reduction = ((orig_size - new_size) / orig_size) * 100 if orig_size > 0 else 0
                    
                    print("\n===========================================")
                    self.print_guide("任务处理完成！操作执行总结：", "SUCCESS")
                    self.print_guide("▶ 格式轨迹: 从 " + Path(input_path).suffix.upper() + " 转换为 " + Path(output_path).suffix.upper(), "INFO")
                    
                    if choice == "1":
                        cat_str = "动漫算法组" if video_category == "1" else "录屏静态优化组" if video_category == "2" else "实拍胶片保护组"
                        self.print_guide("▶ 视频处理: 成功载入 " + target_vcodec.upper() + " " + cat_str, "INFO")
                        if ext_audio and ext_audio!= "none":
                            self.print_guide("▶ 音轨注入: 外部音频无缝合并成功 (" + Path(ext_audio).name + ")", "INFO")
                        elif ext_audio == "none":
                            self.print_guide("▶ 音频处理: 用户指定无声化，已剔除原音轨", "INFO")
                        else:
                            self.print_guide("▶ 音频处理: 原音轨提取与自适应降级压缩", "INFO")
                    else:
                        audio_formats = ("mp3", "flac", "wav", "m4a", "aac", "ogg", "wma", "opus", "ac3")
                        image_formats = ("gif", "webp")
                        if target_fmt in audio_formats:
                            msg = "纯音频重编码压缩" if compress_level else "纯音频极速无损剥离"
                            self.print_guide("▶ 操作类型: " + msg, "INFO")
                        elif target_fmt in image_formats:
                            self.print_guide("▶ 操作类型: 视频片段转双趟高画质动态图像", "INFO")
                        elif compress_level:
                            self.print_guide("▶ 操作类型: 全场景自适应格式转换与 " + target_vcodec.upper() + " 重压缩", "INFO")
                        else:
                            self.print_guide("▶ 操作类型: 毫秒级极速无损外壳封包 (Direct Stream Copy)", "INFO")
                        
                    self.print_guide("▶ 空间利用: 原始大小 " + str(round(orig_size, 2)) + " MB  ->  最终体积 " + str(round(new_size, 2)) + " MB", "INFO")
                    
                    if reduction > 0:
                        self.print_guide("▶ 压缩结果: 成功释放了 " + str(round(reduction, 2)) + "% 的磁盘空间", "SUCCESS")
                    else:
                        self.print_guide("▶ 压缩结果: 增大了 " + str(round(abs(reduction), 2)) + "% (这表明源文件之前已被极度压缩处理过)", "WARNING")
                    print("===========================================\n")
                except Exception:
                    pass

            cont = input("\n所有任务已完毕，是否继续处理下一个文件？(y/n, 默认直接回车退出): ").strip().lower()
            if cont!= 'y':
                self.print_guide("感谢您的使用，视频处理中枢已安全关闭。", "SUCCESS")
                break

if __name__ == "__main__":
    app = FFmpegOrchestrator()
    app.run_interactive_ui()