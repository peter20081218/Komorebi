#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import subprocess
import sys
import os
import re
import time
import json
import threading
import traceback
import concurrent.futures
from pathlib import Path
import atexit
import ctypes    # <--- 新增：用于调用底层系统 API
import platform  # <--- 新增：用于判断当前是否是 Windows 系统

# 强制系统输出采用 UTF-8 编码，彻底解决中文字符输出乱码问题
sys.stdout.reconfigure(encoding='utf-8')
if os.name == 'nt':
    os.system('chcp 65001 > nul 2>&1')

class FFmpegOrchestrator:
    def __init__(self):
        self.ffmpeg_cmd = "ffmpeg"
        self.ffprobe_cmd = "ffprobe"
        self.print_lock = threading.Lock()
        
        # --- 进程追踪池与进程锁 ---
        self.active_processes = list()
        self.process_lock = threading.Lock()
        atexit.register(self.cleanup_processes)  # 应对正常退出
        
        # --- 新增：终极防御装甲，专门拦截 Windows CMD 窗口的红色 X 按钮 ---
        if platform.system() == "Windows":
            def console_ctrl_handler(ctrl_type):
                # 2 代表 CTRL_CLOSE_EVENT (点击了右上角的 X 按钮)
                if ctrl_type == 2:
                    self.cleanup_processes()
                return False  # 返回 False 让系统继续执行原有的关闭操作
            
            # 将回调函数挂载在 self 上，防止被 Python 的垃圾回收机制(GC)清理掉导致崩溃
            self._win_handler = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)(console_ctrl_handler)
            ctypes.windll.kernel32.SetConsoleCtrlHandler(self._win_handler, True)
        # ------------------------------------------------------------------
        
        self.check_ffmpeg_installed()
        self.available_hw_encoders = self.probe_hardware_encoders()

    # --- 新增：专门负责强杀所有后台 ffmpeg 的方法 ---
    def cleanup_processes(self):
        with self.process_lock:
            for p in self.active_processes:
                try:
                    p.kill()  # 无情地杀掉 FFmpeg 进程
                except Exception:
                    pass
            self.active_processes.clear()

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
        """利用 ffprobe 智能探测视频源文件属性，返回体积、时长与视音频编码格式"""
        info = dict()
        try:
            size_mb = os.path.getsize(filepath) / (1024 * 1024)
            info.update(size_mb=size_mb)
            
            cmd_probe = tuple((self.ffprobe_cmd, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", filepath))
            res_probe = subprocess.run(cmd_probe, capture_output=True, text=True, encoding='utf-8', shell=False)
            probe_data = json.loads(res_probe.stdout)
            
            duration = float(probe_data.get("format", dict()).get("duration", 0))
            info.update(duration=duration)
            
            v_codec = "无"
            a_codec = "无"
            width = 1920
            height = 1080
            
            for stream in probe_data.get("streams", tuple()):
                if stream.get("codec_type") == "video" and v_codec == "无":
                    v_codec = stream.get("codec_name", "未知")
                    width = int(stream.get("width", 1920))
                    height = int(stream.get("height", 1080))
                elif stream.get("codec_type") == "audio" and a_codec == "无":
                    a_codec = stream.get("codec_name", "未知")
                    
            info.update(v_codec=v_codec, a_codec=a_codec, width=width, height=height)
            
        except Exception:
            info.update(size_mb=0.0, duration=0.0, v_codec="未知", a_codec="未知", width=1920, height=1080)
        return info

    def validate_and_create_path(self, user_path, is_dir=False):
        path_obj = Path(user_path).resolve()
        if is_dir:
            path_obj.mkdir(parents=True, exist_ok=True)
        return str(path_obj)

    def _estimate_size_and_loss(self, orig_mb, duration, width, height, src_vcodec, target_codec, level, use_hw):
        """全新的精确体积与画质预测算法（基于分辨率、时长与基准码率）"""
        if orig_mb <= 0 or duration <= 0:
            return "未知", "未知"
            
        # 模型A：基于源编码与体积的衰减预测
        src_vcodec = src_vcodec.lower() if isinstance(src_vcodec, str) else "未知"
        if "mpeg" in src_vcodec or "h263" in src_vcodec or "wmv" in src_vcodec:
            src_factor = 0.35 # 老旧编码体积虚胖，潜力极大
        elif "hevc" in src_vcodec or "av1" in src_vcodec or "vp9" in src_vcodec:
            src_factor = 1.2  # 已是高效编码，几乎无法再压
        else:
            src_factor = 0.8  # 标准H264

        tgt_factor = 1.0
        if target_codec == "hevc": tgt_factor = 0.65
        elif target_codec == "av1": tgt_factor = 0.5
            
        hw_factor = 1.5 if use_hw else 1.0 # 硬件编码器为了速度，体积通常大50%
        
        level_factor_map = dict((("1", 1.5), ("2", 0.8), ("3", 0.5), ("4", 0.3)))
        lvl_factor = level_factor_map.get(level, 0.8)
        
        est_from_size = orig_mb * src_factor * tgt_factor * hw_factor * lvl_factor
        
        # 模型B：基于时长与分辨率绝对码率的基准预测
        pixel_ratio = (width * height) / (1920.0 * 1080.0)
        if pixel_ratio <= 0: pixel_ratio = 1.0
        
        base_kbps = dict((("1", 8000), ("2", 4000), ("3", 2000), ("4", 1000))).get(level, 4000)
        codec_mult = 1.0 if target_codec == "h264" else (0.7 if target_codec == "hevc" else 0.55)
        
        est_from_duration = (base_kbps * pixel_ratio * hw_factor * codec_mult * duration) / 8192.0
        
        # 混合两者，取平均以应对各种极端源文件
        final_est = (est_from_size + est_from_duration) / 2.0
        
        if final_est < orig_mb * 0.05:
            final_est = orig_mb * 0.05
            
        loss_eval = dict((("1", "<1% (无损级)"), ("2", "~5% (极低损)"), ("3", "~15% (明显压缩)"), ("4", "~30% (画质劣化)")))
        return str(round(final_est, 1)), loss_eval.get(level, "未知")

    def _estimate_audio_size(self, duration, kbps):
        """基于时长的精确纯音频体积预测"""
        if duration <= 0:
            return "未知"
        return str(round((kbps * duration) / 8192.0, 1))

    def build_video_compress_command(self, input_file, output_file, video_category, quality_level, target_vcodec, use_hw=True, ext_audio=None):
        """构建全场景差异化高保真画质与压缩的独立算法参数链"""
        cmd = list()
        
        # 外部音轨合并与映射逻辑 (庞大无损音轨自动压缩)
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

        level_crf_map = dict((("1", "19"), ("2", "23"), ("3", "27"), ("4", "31")))
        cq_val = level_crf_map.get(quality_level, "23")
        
        x265_params = ""
        svtav1_params = ""
        hw_preset = "p6"
        
        # 三大场景独立算法预设
        if video_category == "1": # 动漫
            if quality_level in tuple(("1", "2")):
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
            if quality_level in tuple(("1", "2")):
                x265_params = "no-sao=1:bframes=4:psy-rd=1.5:psy-rdoq=2.0:aq-mode=2"
                svtav1_params = "tune=2:film-grain=8"
                hw_preset = "p7"
            else:
                x265_params = "limit-sao=1:bframes=4:psy-rd=1.0:aq-mode=2"
                svtav1_params = "tune=2:film-grain=4"
                hw_preset = "p5"

        # 编码器硬件自适应引擎
        if use_hw and len(self.available_hw_encoders) > 0:
            if "nvenc" in self.available_hw_encoders:
                if target_vcodec == "av1" and "av1_nvenc" in self.available_hw_encoders:
                    encoder = "av1_nvenc"
                else:
                    encoder = "hevc_nvenc" if target_vcodec == "hevc" else "h264_nvenc"
                
                hw_args = list()
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
        
        # 保护防崩机制：如果目标是 H.264，不论软硬解都强制降级为 8-bit 色深
        if target_vcodec == "h264":
            cmd.extend(("-pix_fmt", "yuv420p"))

        cmd.append(output_file)
        return cmd

    def build_format_factory_command(self, input_file, output_file, format_type, compress_level=None, use_hw=True, target_vcodec="h264", ext_audio=None, a_codec="未知", v_codec="未知"):
        """构建全生态格式化工厂，具备强大的智能容器纠错拦截功能"""
        cmd = list()
        
        format_type = format_type.lower()
        audio_formats = tuple(("mp3", "flac", "wav", "m4a", "aac", "ogg", "wma", "opus", "ac3"))
        image_formats = tuple(("gif", "webp"))
        
        if ext_audio and ext_audio!= "none":
            cmd.extend((self.ffmpeg_cmd, "-y", "-i", input_file, "-i", ext_audio))
            if format_type not in audio_formats and format_type not in image_formats:
                cmd.extend(("-map", "0:v", "-map", "1:a"))
            else:
                cmd.extend(("-map", "1:a"))
        elif ext_audio == "none":
            cmd.extend((self.ffmpeg_cmd, "-y", "-i", input_file))
            if format_type not in audio_formats and format_type not in image_formats:
                cmd.extend(("-map", "0:v"))
        else:
            cmd.extend((self.ffmpeg_cmd, "-y", "-i", input_file))
            if format_type not in audio_formats and format_type not in image_formats:
                cmd.extend(("-map", "0"))

        # --- 核心：智能纠错防崩拦截系统 ---
        v_codec_lower = v_codec.lower() if isinstance(v_codec, str) else "未知"
        a_codec_lower = a_codec.lower() if isinstance(a_codec, str) else "未知"
        
        if not compress_level:
            if format_type in audio_formats:
                if format_type == "mp3" and "mp3" not in a_codec_lower:
                    self.print_guide("【智能修正】原音频无法无损装入 MP3，已自动热切换为 320k 高品质转码！", "WARNING")
                    compress_level = "1"
                elif format_type in tuple(("flac", "wav")) and a_codec_lower not in tuple(("flac", "pcm_s16le", "pcm_s24le", "pcm_s32le", "wav", "pcm_f32le")):
                    self.print_guide("【智能修正】原音频不是无损格式，已自动热切换为无损转码导出！", "WARNING")
                    compress_level = "1"
                elif format_type == "opus" and "opus" not in a_codec_lower:
                    compress_level = "2"
                elif format_type == "ogg" and a_codec_lower not in tuple(("vorbis", "opus")):
                    compress_level = "2"
                elif format_type in tuple(("aac", "m4a")) and "aac" not in a_codec_lower:
                    compress_level = "2"
            elif format_type not in image_formats and format_type!= "3gp":
                if format_type == "webm" and (v_codec_lower not in tuple(("vp8", "vp9", "av1")) or a_codec_lower not in tuple(("vorbis", "opus", "无", "none", "未知"))):
                    self.print_guide("【智能修正】原编码与 WebM 容器不兼容，已自动启用 AV1+Opus 转码保障完成！", "WARNING")
                    compress_level = "2"
                    target_vcodec = "av1"
                elif format_type == "mp4" and v_codec_lower not in tuple(("h264", "hevc", "av1", "mpeg4", "未知")):
                    self.print_guide("【智能修正】原视频编码与 MP4 容器冲突，已自动启用高保真 H.264 转码！", "WARNING")
                    compress_level = "2"
                    target_vcodec = "h264"
                elif format_type == "flv" and v_codec_lower not in tuple(("h264", "flv", "h263", "未知")):
                    self.print_guide("【智能修正】原视频编码与 FLV 容器冲突，已自动启用高保真 H.264 转码！", "WARNING")
                    compress_level = "2"
                    target_vcodec = "h264"
        # -----------------------------------
        
        if format_type in audio_formats:
            cmd.append("-vn") 
            if compress_level:
                audio_kbps = dict((("1", "320k"), ("2", "192k"), ("3", "128k"), ("4", "64k")))
                target_br = audio_kbps.get(compress_level, "192k")
                
                if format_type == "mp3":
                    cmd.extend(("-c:a", "libmp3lame", "-b:a", target_br)) 
                elif format_type in tuple(("flac", "wav")):
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
                # 避免渲染器吃括号的转义手段
                gif_filter = "fps=15,scale=480:-1:flags=lanczos,splitLs0RLs1R;Ls0RpalettegenLpR;Ls1RLpRpaletteuse=dither=sierra2_4a".replace("L", chr(91)).replace("R", chr(93))
                cmd.extend(("-vf", gif_filter, "-loop", "0"))
            else:
                cmd.extend(("-vcodec", "libwebp", "-lossless", "0", "-qscale", "75", "-preset", "default", "-loop", "0", "-an"))

        elif format_type == "3gp":
            cmd.extend(("-c:v", "h263", "-s", "352x288", "-r", "15", "-c:a", "libopencore_amrnb", "-ar", "8000", "-ac", "1"))
            
        else: # 常规视频互转
            if compress_level:
                level_crf_map = dict((("1", "19"), ("2", "23"), ("3", "27"), ("4", "31")))
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
        if "No such file or directory" in error_log:
            return "找不到输入文件。\n(解决办法): 请检查文件路径中是否包含系统无法识别的罕见特殊符号，或尝试将源视频直接移至电脑根目录后重试。", True
        if "10 bit encode not supported" in error_log:
            return "您的硬件引擎过于老旧，不支持10位色深的视频压制。\n(解决办法): 请在压制时选择 H.264 以外的格式，或关闭硬件加速。", True
        if "Error while opening encoder" in error_log or "No capable devices found" in error_log:
            return "编码器初始化失败。当前显卡不支持您所选的编码格式（如极老显卡不支持HEVC）。\n(解决办法): 请更新驱动程序，或退回使用 H.264，或使用纯 CPU 模式。", True
        if "AVERROR_INVALIDDATA" in error_log or "Invalid data found" in error_log:
            return "源视频数据无效或文件头部破损。\n(解决办法): 视频极有可能未下载完整，或者被其他播放器占用了写入权限。请关闭其他程序后再试。", True
        if "Out of memory" in error_log or "cannot allocate memory" in error_log:
            return "系统资源耗尽。当前计算机内存或显存已满。\n(解决办法): 请关闭游戏等占用大量内存的软件，或在批量处理时调低并发线程数。", True
        if "Invalid audio stream" in error_log or "incorrect codec parameters" in error_log or "Exactly one MP3 audio stream is required" in error_log:
            return "格式与编码物理冲突。您试图将不兼容的流（如 AAC）无损塞入严格限制的容器中（如 MP3）。\n(解决办法): 智能修正系统已尽力拦截，若仍报错请手动选择画质挡位进行强制转码。", True
        return "发生了FFmpeg底层异常崩溃。", False

    def execute_with_fallback(self, cmd, input_file="", output_file="", out_dir="", **kwargs):
        cmd_str = " ".join(cmd)
        process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, 
                                  text=True, encoding='utf-8', errors='ignore', shell=False)
        
        # --- 新增：将新启动的进程加入追踪池 ---
        with self.process_lock:
            self.active_processes.append(process)
        # ------------------------------------
        error_buffer = ""
        duration_secs = 0
        start_time = time.time()
        
        # 新增：用于批量处理的标识
        is_batch = kwargs.get('is_batch', False)
        
        for line in process.stderr:
            error_buffer += line
            
            if duration_secs == 0:
                match_duration = re.search(r"Duration:\s*(\d\d):(\d\d):(\d\d\.\d+)", line)
                if match_duration:
                    duration_secs = (int(match_duration.group(1)) * 3600 +
                                    int(match_duration.group(2)) * 60 +
                                    float(match_duration.group(3)))
            
            if duration_secs > 0:
                match_time = re.search(r"time=\s*(\d\d):(\d\d):(\d\d\.\d+)", line)
                if match_time:
                    current_secs = (int(match_time.group(1)) * 3600 +
                                int(match_time.group(2)) * 60 +
                                float(match_time.group(3)))
                    
                    # --- 新增：全局多线程统一进度条 与 单文件进度条 分离系统 ---
                    if is_batch and hasattr(self, 'batch_total_duration'):
                        # 加锁保护，防止多个工作线程同时写屏幕导致画面撕裂
                        with self.print_lock:
                            self.file_progress_dict[input_file] = current_secs
                            total_current = sum(self.file_progress_dict.values())
                            percent = min((total_current / self.batch_total_duration) * 100, 100.0) if self.batch_total_duration > 0 else 0
                            
                            elapsed = time.time() - self.batch_start_time
                            if elapsed > 0 and total_current > 0:
                                speed = total_current / elapsed
                                eta = max((self.batch_total_duration - total_current) / speed, 0)
                                eta_str = f"{int(eta // 60)}分{int(eta % 60)}秒"
                            else:
                                eta_str = "计算中..."
                            
                            # 批量模式进度条 (降低刷新频率防闪烁，尾部加空格清空残留字符)
                            if percent - getattr(self, 'last_batch_percent', 0) >= 0.2 or percent > 99:
                                bar_len = 35
                                filled_len = int(bar_len * percent // 100)
                                bar = '█' * filled_len + '-' * (bar_len - filled_len)
                                sys.stdout.write(f"\r(全体总进度) [{bar}] {percent:.1f}% | 总剩余约: {eta_str}    ")
                                sys.stdout.flush()
                                self.last_batch_percent = percent
                    else:
                        # 原始单文件模式独立进度条
                        percent = min((current_secs / duration_secs) * 100, 100.0)
                        elapsed = time.time() - start_time
                        if elapsed > 0 and current_secs > 0:
                            speed = current_secs / elapsed
                            eta = max((duration_secs - current_secs) / speed, 0)
                            eta_str = f"{int(eta // 60)}分{int(eta % 60)}秒"
                        else:
                            eta_str = "计算中..."
                        
                        bar_len = 30
                        filled_len = int(bar_len * percent // 100)
                        bar = '█' * filled_len + '-' * (bar_len - filled_len)
                        sys.stdout.write(f"\r(系统提示) 处理进度: [{bar}] {percent:.1f}% | 剩余约: {eta_str}    ")
                        sys.stdout.flush()

        process.wait()
        # --- 新增：任务正常结束后，从追踪池中除名 ---
        with self.process_lock:
            if process in self.active_processes:
                self.active_processes.remove(process)
        # --------------------------------------------
        
        # 一个子任务完成后，将其在计分板上的进度强制拉满，防止误差导致总进度卡在 99%
        if is_batch and hasattr(self, 'file_progress_dict') and duration_secs > 0:
            with self.print_lock:
                self.file_progress_dict[input_file] = duration_secs

        exit_code = process.returncode
        if exit_code!= 0:
            self.print_guide("\n【中止】" + Path(input_file).name + " 执行失败！正在分析原因...", "ERROR")
            diagnostic_msg, is_known_error = self.diagnostic_error_translator(error_buffer)
            self.print_guide("【故障诊断】" + diagnostic_msg, "WARNING")
            
            if not is_known_error:
                if not out_dir:
                    out_dir = os.path.dirname(os.path.abspath(input_file))
                log_file = os.path.join(out_dir, "ffmpeg_error_log_" + str(int(time.time())) + ".txt")
                try:
                    with open(log_file, "w", encoding="utf-8") as f:
                        f.write("========== FFmpeg Crash Log ==========\n")
                        f.write("Command:\n" + cmd_str + "\n\n")
                        f.write("System stderr Output:\n" + error_buffer)
                    self.print_guide("由于无法精准判明原因，系统已将完整的报错代码生成日志！", "INFO")
                    self.print_guide("【日志文件已保存在】: " + log_file, "INFO")
                except Exception:
                    self.print_guide("日志文件写入失败，请检查输出目录权限。", "ERROR")

            hw_error_triggers = tuple(("10 bit encode not supported", "No capable devices found", "Error while opening encoder", "AVERROR", "unsupported device"))
            is_hw_failure = False
            for trigger in hw_error_triggers:
                if trigger in error_buffer:
                    is_hw_failure = True
                    break
            
            is_hw_used = "nvenc" in cmd_str or "qsv" in cmd_str or "amf" in cmd_str
            is_video_compress = kwargs.get("is_video_compress", False)
            
            if is_video_compress and is_hw_failure and "libx265" not in cmd and "libx264" not in cmd and "libsvtav1" not in cmd:
                self.print_guide("【触发容灾机制】显卡加速接口瘫痪，正在为该文件自动热切换至纯 CPU 软压制重试！", "WARNING")
                if kwargs.get("mode") == "2":
                    fallback_cmd = self.build_format_factory_command(
                        input_file, output_file,
                        format_type=kwargs.get("format_type"),
                        compress_level=kwargs.get("compress_level"),
                        use_hw=False,
                        target_vcodec=kwargs.get("target_vcodec"),
                        ext_audio=kwargs.get("ext_audio"),
                        a_codec=kwargs.get("a_codec", "未知"),
                        v_codec=kwargs.get("v_codec", "未知")
                    )
                else:
                    fallback_cmd = self.build_video_compress_command(
                        input_file, output_file, 
                        video_category=kwargs.get("video_category", "1"),
                        quality_level=kwargs.get("quality_level", "2"), 
                        target_vcodec=kwargs.get("target_vcodec", "hevc"),
                        use_hw=False, 
                        ext_audio=kwargs.get("ext_audio")
                    )
                kwargs.update(use_hw=False)
                return self.execute_with_fallback(fallback_cmd, input_file=input_file, output_file=output_file, out_dir=out_dir, **kwargs) 
            else:
                self.print_guide("任务不可挽回地失败。请参考上述排障指南处理。", "ERROR")
                return False
        else:
            self.print_guide("【完成】" + Path(input_file).name + " 处理成功并已保存！", "SUCCESS")
            return True

    def _prompt_audio_injection(self, media_info, is_batch=False):
        has_audio = media_info.get("a_codec")!= "无"
        if not has_audio:
            if is_batch:
                self.print_guide("【侦测警报】发现无声视频。处于一键批量模式中，默认保留原样(纯无声视频)。", "WARNING")
                return "none"
            self.print_guide("【侦测警报】原视频文件中不存在声音/音频轨道！", "WARNING")
            add_audio = input("是否需要混入外部音轨/音乐？(y: 合并外部音频 / n: 保持无声纯画面, 默认n): ").strip().lower()
            if add_audio == 'y':
                ext_audio = input("请拖入或粘贴外部音频文件路径: ").strip().strip('"').strip("'")
                return self.validate_and_create_path(ext_audio)
            else:
                return "none"
        return None

    def _ask_settings(self, input_path, choice, is_batch=False):
        media_info = self.check_media_info(input_path)
        orig_mb = media_info.get("size_mb", 0)
        orig_duration = media_info.get("duration", 0)
        orig_w = media_info.get("width", 1920)
        orig_h = media_info.get("height", 1080)
        src_codec = media_info.get("v_codec", "")
        
        print("\n-------------------------------------------")
        if is_batch:
            self.print_guide(">>> 正在录入【全局批量处理规则】(以下预测基于首个文件： " + Path(input_path).name + ")", "INFO")
            
        self.print_guide("源文件侦测: 体积 " + str(round(orig_mb, 2)) + " MB | 时长: " + str(round(orig_duration, 1)) + "秒 | 分辨率: " + str(orig_w) + "x" + str(orig_h), "INFO")
        self.print_guide("编码状态: [视频] " + src_codec.upper() + " | [音频] " + media_info.get("a_codec").upper(), "INFO")
        
        use_hw_flag = False
        supported_codecs = list()
        supported_codecs.append("H.264 (AVC)")
        
        if len(self.available_hw_encoders) > 0:
            use_hw_flag = True
            self.print_guide("检测到系统搭载硬件加速引擎: " + ", ".join(self.available_hw_encoders).upper(), "SUCCESS")
            if any("hevc" in enc for enc in self.available_hw_encoders):
                supported_codecs.append("H.265 (HEVC)")
            if any("av1" in enc for enc in self.available_hw_encoders):
                supported_codecs.append("AV1 (AOM)")
        else:
            self.print_guide("系统未检测到GPU编码器，将为您调度纯净的高画质 CPU 引擎。", "WARNING")
            supported_codecs.extend(tuple(("H.265 (HEVC)", "AV1 (AOM)")))
        
        self.print_guide("推荐可用的目标编码: " + " / ".join(supported_codecs), "INFO")
        print("-------------------------------------------")
        
        settings = dict()
        settings.update(choice=choice, v_codec=src_codec, a_codec=media_info.get("a_codec", "未知"))
        
        if choice == "1":
            print("\n请选择您要压制的视频场景 (系统将套用各场景专属优化算法)：")
            print("  1. 动漫 / 二次元 (专属去色带与线条锐利保护)")
            print("  2. 电脑录屏 / 游戏 (极高压缩率，保护文字界面不发虚)")
            print("  3. 实拍视频 / 电影 (保留真实噪点，优化复杂物理光影动态)")
            video_category = input("请输入类型数字 (1-3, 默认1): ").strip()
            if video_category not in tuple(("1", "2", "3")):
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
                
            if target_fmt == "webm" and target_vcodec in tuple(("h264", "hevc")):
                self.print_guide("【兼容性保护】WebM 容器规范严格禁止H.264/HEVC，已自动为您将内核切换为开源的 AV1。", "WARNING")
                target_vcodec = "av1"
            
            print("\n请选择该场景下的压缩预设挡位 (基于混合AI算法为您精确估算)：")
            s1, l1 = self._estimate_size_and_loss(orig_mb, orig_duration, orig_w, orig_h, src_codec, target_vcodec, "1", use_hw_flag)
            s2, l2 = self._estimate_size_and_loss(orig_mb, orig_duration, orig_w, orig_h, src_codec, target_vcodec, "2", use_hw_flag)
            s3, l3 = self._estimate_size_and_loss(orig_mb, orig_duration, orig_w, orig_h, src_codec, target_vcodec, "3", use_hw_flag)
            s4, l4 = self._estimate_size_and_loss(orig_mb, orig_duration, orig_w, orig_h, src_codec, target_vcodec, "4", use_hw_flag)
            print("  1. 极致高保真 (预计画质损失: " + l1 + " | 预估转出: ~" + s1 + " MB)")
            print("  2. 优质归档   (预计画质损失: " + l2 + " | 预估转出: ~" + s2 + " MB)")
            print("  3. 日常流媒体 (预计画质损失: " + l3 + " | 预估转出: ~" + s3 + " MB)")
            print("  4. 极限存储   (预计画质损失: " + l4 + " | 预估转出: ~" + s4 + " MB)")
            quality_level = input("请输入挡位数字 (1-4, 默认2): ").strip()
            if quality_level not in tuple(("1", "2", "3", "4")):
                quality_level = "2"
                
            ext_audio = self._prompt_audio_injection(media_info, is_batch)
            
            settings.update(video_category=video_category, target_vcodec=target_vcodec, target_fmt=target_fmt, quality_level=quality_level, ext_audio=ext_audio)
            
        elif choice == "2":
            print("\n【支持的视频格式】: mp4, mkv, avi, mov, flv, wmv, webm, 3gp, ts, m2ts, vob, rmvb, ogv 等")
            print("【支持的动图格式】: gif, webp")
            print("【支持的音频格式】: mp3, flac, wav, aac, m4a, ogg, wma, opus, ac3 等")
            target_fmt = input("请输入您想要转换的最终格式后缀: ").strip().lower()
            
            compress_level = None
            target_vcodec = "h264"
            ext_audio = None
            audio_formats = tuple(("mp3", "flac", "wav", "m4a", "aac", "ogg", "wma", "opus", "ac3"))
            image_formats = tuple(("gif", "webp"))
            
            if target_fmt in audio_formats:
                print("\n是否对音频启用高级压缩引擎？")
                a1 = self._estimate_audio_size(orig_duration, 320)
                a2 = self._estimate_audio_size(orig_duration, 192)
                a3 = self._estimate_audio_size(orig_duration, 128)
                a4 = self._estimate_audio_size(orig_duration, 64)
                print("  1. 极高音质归档 (320kbps | 预估体积: ~" + a1 + " MB)")
                print("  2. 高保真音乐   (192kbps | 预估体积: ~" + a2 + " MB)")
                print("  3. 平衡流媒体   (128kbps | 预估体积: ~" + a3 + " MB)")
                print("  4. 极致语音压缩 (64kbps | 预估体积: ~" + a4 + " MB)")
                print("  n. 原音轨极速直通提取 (自动修正冲突, 默认)")
                opt = input("请输入选项 (1-4 / n, 默认 n): ").strip().lower()
                if opt in tuple(("1", "2", "3", "4")):
                    compress_level = opt
            elif target_fmt not in image_formats:
                ext_audio = self._prompt_audio_injection(media_info, is_batch)
                print("\n是否在视频转换的同时进行瘦身压缩？")
                print("  y: 启用压缩 (自适应选择编码器与画质)")
                print("  n: 仅转换外壳格式 (极速无损 Direct Stream Copy)")
                opt = input("请输入选项 (y/n, 默认 n): ").strip().lower()
                
                if opt == 'y':
                    print("\n请选择该格式承载的视频内核：")
                    print("  1. H.264 (兼容性之王, 默认)")
                    print("  2. H.265 (超高性价比)")
                    print("  3. AV1   (次世代极限压缩)")
                    c_opt = input("请输入 (1-3, 默认 1): ").strip()
                    if c_opt == "2": target_vcodec = "hevc"
                    elif c_opt == "3": target_vcodec = "av1"
                    
                    if target_fmt == "webm" and target_vcodec in tuple(("h264", "hevc")):
                        self.print_guide("【兼容性修正】WebM 容器仅支持 AV1/VP9 编码，已自动为您将编码器切换至 AV1。", "WARNING")
                        target_vcodec = "av1"
                        
                    print("\n请选择格式转换的压缩预设挡位 (系统已为您动态预估结果)：")
                    s1, l1 = self._estimate_size_and_loss(orig_mb, orig_duration, orig_w, orig_h, src_codec, target_vcodec, "1", use_hw_flag)
                    s2, l2 = self._estimate_size_and_loss(orig_mb, orig_duration, orig_w, orig_h, src_codec, target_vcodec, "2", use_hw_flag)
                    s3, l3 = self._estimate_size_and_loss(orig_mb, orig_duration, orig_w, orig_h, src_codec, target_vcodec, "3", use_hw_flag)
                    s4, l4 = self._estimate_size_and_loss(orig_mb, orig_duration, orig_w, orig_h, src_codec, target_vcodec, "4", use_hw_flag)
                    print("  1. 极高画质 (预计画质损失: " + l1 + " | 预估转出: ~" + s1 + " MB)")
                    print("  2. 标准画质 (预计画质损失: " + l2 + " | 预估转出: ~" + s2 + " MB)")
                    print("  3. 较低画质 (预计画质损失: " + l3 + " | 预估转出: ~" + s3 + " MB)")
                    print("  4. 极低画质 (预计画质损失: " + l4 + " | 预估转出: ~" + s4 + " MB)")
                    compress_level = input("请输入挡位数字 (1-4, 默认2): ").strip()
                    if compress_level not in tuple(("1", "2", "3", "4")):
                        compress_level = "2"

            settings.update(target_fmt=target_fmt, compress_level=compress_level, target_vcodec=target_vcodec, ext_audio=ext_audio)
            
        return settings

    def _execute_worker(self, input_file, out_dir, settings, is_batch=False):
        """线程池分发工作的执行实体"""
        base_name = Path(input_file).stem
        choice = settings.get("choice")
        target_fmt = settings.get("target_fmt", "mkv")

        success = False
        output_path = str(Path(out_dir) / (base_name + "_converted." + target_fmt)) if choice == "2" else str(Path(out_dir) / (base_name + "_compressed." + target_fmt))

        if choice == "1":
            cmd = self.build_video_compress_command(
                input_file, output_path, 
                video_category=settings.get("video_category"), 
                quality_level=settings.get("quality_level"), 
                target_vcodec=settings.get("target_vcodec"), 
                use_hw=True, 
                ext_audio=settings.get("ext_audio")
            )
            success = self.execute_with_fallback(
                cmd, mode="1", input_file=input_file, output_file=output_path, out_dir=out_dir,
                video_category=settings.get("video_category"),
                quality_level=settings.get("quality_level"),
                target_vcodec=settings.get("target_vcodec"),
                ext_audio=settings.get("ext_audio"),
                is_video_compress=True,
                is_batch=is_batch  # 修复2：加上这一行，让底层进度条知道现在是批量模式
            )
        elif choice == "2":
            cmd = self.build_format_factory_command(
                input_file, output_path, target_fmt, 
                compress_level=settings.get("compress_level"), 
                use_hw=True, 
                target_vcodec=settings.get("target_vcodec"), 
                ext_audio=settings.get("ext_audio"),
                a_codec=settings.get("a_codec", "未知"),
                v_codec=settings.get("v_codec", "未知")
            )
            success = self.execute_with_fallback(
                cmd, mode="2", input_file=input_file, output_file=output_path, out_dir=out_dir, is_batch=is_batch,
                format_type=target_fmt,
                compress_level=settings.get("compress_level"),
                target_vcodec=settings.get("target_vcodec"),
                ext_audio=settings.get("ext_audio"),
                a_codec=settings.get("a_codec", "未知"),
                v_codec=settings.get("v_codec", "未知"),
                is_video_compress=False
            )
            
        if success and os.path.exists(output_path):
            try:
                orig_size = os.path.getsize(input_file) / (1024 * 1024)
                new_size = os.path.getsize(output_path) / (1024 * 1024)
                reduction = ((orig_size - new_size) / orig_size) * 100 if orig_size > 0 else 0
                
                self.print_guide(">>> 任务完成总结: (" + base_name + ") 空间缩减: " + str(round(reduction, 1)) + "% | 最终大小: " + str(round(new_size, 1)) + " MB", "SUCCESS")
            except Exception:
                pass

    def run_interactive_ui(self):
        """主入口包含【Python级别防御装甲】，杜绝任何未处理异常导致的闪退"""
        while True:
            try:
                self.print_guide("\n===========================================", "INFO")
                self.print_guide("  全场景音视频压制与多线程格式化工厂中枢  ", "INFO")
                self.print_guide("===========================================", "INFO")
                print("请选择操作模式：\n  1. 场景化视频极致压缩 (针对动漫/录屏/拍摄独立定制算法)\n  2. 全能格式化工厂 (支持全音视频互转/防崩容灾处理)")
                
                choice = input("\n请输入选项数字 (1/2): ").strip()
                if choice not in tuple(("1", "2")):
                    self.print_guide("无效选择，请重新输入。", "ERROR")
                    continue
                
                input_path = input("\n请拖入或粘贴需要处理的文件或【文件夹目录】路径 (完美支持多文件批量): ").strip().strip('"').strip("'")
                input_path = self.validate_and_create_path(input_path)
                
                out_dir = input("\n请输入自定义的输出文件夹路径 (直接回车保存在源目录同级): ").strip().strip('"').strip("'")
                if not out_dir:
                    out_dir = str(Path(input_path).parent if os.path.isfile(input_path) else input_path)
                else:
                    out_dir = self.validate_and_create_path(out_dir, is_dir=True)
                    
                valid_exts = tuple((".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".3gp", ".ts", ".m2ts", ".rmvb", ".vob", ".ogv", ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".wma", ".opus"))

                # 目录批量收集系统
                files = list()
                if os.path.isdir(input_path):
                    for f in Path(input_path).iterdir():
                        if f.is_file() and f.suffix.lower() in valid_exts:
                            files.append(str(f))
                    if len(files) == 0:
                        self.print_guide("目录中未找到任何支持的音视频文件！", "ERROR")
                        continue
                    self.print_guide("成功解析目录！共包含 " + str(len(files)) + " 个可处理的多媒体文件。", "SUCCESS")
                    batch_opt = input("是否对该目录下所有文件应用【同一种】转换/压制设置？\n(y: 一键批量自动多线程处理 / n: 为每个文件分别作单独设置) 默认 y: ").strip().lower()
                    batch_mode = False if batch_opt == 'n' else True
                else:
                    files.append(input_path)
                    batch_mode = False

                max_workers = 1
                if len(files) > 1:
                    try:
                        w_input = input("\n请设置并发处理数量 (推荐 1-3，过高会耗尽内存导致系统卡死，默认 2): ").strip()
                        max_workers = int(w_input) if w_input else 2
                    except Exception:
                        max_workers = 2

                    # 多线程分发任务引擎
                    if batch_mode:
                        first_file = next(iter(files))
                        settings = self._ask_settings(first_file, choice, is_batch=True)
                        
                        # --- 新增：预计算所有文件的总时长，用于全局进度条 ---
                        self.print_guide("\n>>> 正在扫描全部文件以精准计算总体进度，请稍候...", "INFO")
                        self.batch_total_duration = 0
                        for f in files:
                            self.batch_total_duration += self.check_media_info(f).get("duration", 0)
                        
                        self.batch_start_time = time.time()
                        self.file_progress_dict = {}  # 用于记录每个文件的独立进度
                        self.last_batch_percent = 0.0
                        # ------------------------------------------------

                        self.print_guide(">>> [引擎启动] 正在以 " + str(max_workers) + " 线程并发执行批量队列...\n", "SUCCESS")
                        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                            futures = []
                            for f in files:
                                futures.append(executor.submit(
                                    self._execute_worker, 
                                    f, 
                                    out_dir, 
                                    settings,
                                    is_batch=True  # 标记为批量任务
                                ))                      
                            # 等待所有批量任务跑完后再打印换行，防止打断进度条
                            concurrent.futures.wait(futures)
                            print("\n")                
                            pass
                else:
                    settings_list = list()
                    for idx, f in enumerate(files):
                        if len(files) > 1:
                            self.print_guide("\n>>> 正在配置队列任务 (" + str(idx+1) + "/" + str(len(files)) + "): " + Path(f).name, "INFO")
                        s = self._ask_settings(f, choice, is_batch=False)
                        settings_list.append(tuple((f, s)))
                        
                    self.print_guide("\n>>> [引擎启动] 正在以 " + str(max_workers) + " 线程并发执行专属队列...", "SUCCESS")
                    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                        futures = []
                        for f_path, f_settings in settings_list:
                            futures.append(executor.submit(
                                self._execute_worker,
                                f_path,
                                out_dir,
                                f_settings,
                                is_batch=len(settings_list) > 1  # 多个文件按批量处理
                            ))
                            pass

                cont = input("\n本轮所有任务已完毕，是否继续处理其他文件或目录？(y/n, 默认直接回车退出): ").strip().lower()
                if cont!= 'y':
                    self.print_guide("感谢您的使用，视频处理自动化中枢已安全关闭。", "SUCCESS")
                    break

            except KeyboardInterrupt:
                self.print_guide("\n检测到用户手动强制终止操作，正在强杀后台 FFmpeg 进程，请稍候...", "WARNING")
                self.cleanup_processes()  # 手动触发强杀
                break
                
            except Exception as e:
                # --- 终极 Python 代码防御层：自动拦截任何不可预知的内部逻辑错误 ---
                error_details = traceback.format_exc()
                self.print_guide("\n(致命错误) Python 脚本内部发生未预期的严重崩溃！", "ERROR")
                self.print_guide("错误说明: " + str(e), "ERROR")
                
                log_dir = out_dir if 'out_dir' in locals() and out_dir else os.getcwd()
                log_name = os.path.join(log_dir, "script_crash_log_" + str(int(time.time())) + ".txt")
                try:
                    with open(log_name, "w", encoding="utf-8") as f:
                        f.write("========== Python Script Crash Log ==========\n")
                        f.write(error_details)
                    self.print_guide("系统已成功拦截该崩溃，并将详细故障代码打包为日志文件！", "INFO")
                    self.print_guide("(日志文件路径): " + log_name, "INFO")
                    self.print_guide("由于是底层代码级报错，请将该日志文件提供给开发者以修复此Bug。", "INFO")
                except Exception:
                    pass
                
                cont = input("\n是否要重置系统并尝试新的任务？(y/n, 默认直接回车退出): ").strip().lower()
                if cont!= 'y':
                    break

if __name__ == "__main__":
    app = FFmpegOrchestrator()
    app.run_interactive_ui()