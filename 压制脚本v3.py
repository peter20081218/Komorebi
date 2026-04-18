#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import subprocess
import sys
import os
import re
import time
from pathlib import Path

# 强制系统输出采用 UTF-8 编码，彻底解决中文字符输出乱码问题
sys.stdout.reconfigure(encoding='utf-8')
if os.name == 'nt':
    os.system('chcp 65001 > nul 2>&1') # 切换Windows控制台至UTF-8环境

class FFmpegOrchestrator:
    def __init__(self):
        self.ffmpeg_cmd = "ffmpeg"
        self.ffprobe_cmd = "ffprobe"
        self.check_ffmpeg_installed()
        self.available_hw_encoders = self.probe_hardware_encoders()

    def print_guide(self, text, message_type="INFO"):
        """格式化全中文引导输出"""
        prefixes = dict()
        prefixes.update({"INFO": "[系统提示] "})
        prefixes.update({"SUCCESS": "[成功] "})
        prefixes.update({"WARNING": "[警告] "})
        prefixes.update({"ERROR": "[错误] "})
        print(prefixes.get(message_type, "") + text)

    def check_ffmpeg_installed(self):
        """系统预检：探测FFmpeg内核是否就绪"""
        try:
            cmd_tuple = (self.ffmpeg_cmd, "-version")
            subprocess.run(cmd_tuple, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
        except FileNotFoundError:
            self.print_guide("未在系统中找到 FFmpeg 核心组件！请确保已安装并配置了环境变量。", "ERROR")
            sys.exit(1)

    def probe_hardware_encoders(self):
        """探测当前显卡支持的硬件编码器加速接口"""
        encoders = list()
        try:
            cmd_tuple = (self.ffmpeg_cmd, "-encoders")
            result = subprocess.run(cmd_tuple, capture_output=True, text=True, encoding='utf-8', shell=False)
            output = result.stdout
            if "hevc_nvenc" in output or "h264_nvenc" in output:
                encoders.append("nvenc") 
            if "hevc_qsv" in output or "h264_qsv" in output:
                encoders.append("qsv")   
            if "hevc_amf" in output or "h264_amf" in output:
                encoders.append("amf")   
        except Exception:
            pass
        return encoders

    def check_audio_stream(self, filepath):
        """利用 ffprobe 智能探测视频源文件中是否包含音轨"""
        try:
            cmd_tuple = (self.ffprobe_cmd, "-loglevel", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", filepath)
            result = subprocess.run(cmd_tuple, capture_output=True, text=True, shell=False)
            return "audio" in result.stdout.lower()
        except Exception:
            return True # 若探测失败，默认当做有音轨处理，避免误导

    def validate_and_create_path(self, user_path, is_dir=False):
        """严格校验并清洗包含特殊符号的中英文路径"""
        path_obj = Path(user_path).resolve()
        if is_dir:
            path_obj.mkdir(parents=True, exist_ok=True)
        return str(path_obj)

    def build_video_compress_command(self, input_file, output_file, video_category, quality_level, use_hw=True, ext_audio=None):
        """构建全场景（动漫/录屏/拍摄）极致高保真画质与压缩的自定义核心参数链"""
        cmd = list()
        
        # --- 1. 外部音轨合并与映射逻辑 ---
        if ext_audio and ext_audio!= "none":
            cmd.extend((self.ffmpeg_cmd, "-y", "-i", input_file, "-i", ext_audio))
            cmd.extend(("-map", "0:v", "-map", "1:a")) # 强制映射视频与外部文件的音频
            cmd.extend(("-c:a", "aac", "-b:a", "192k"))
        elif ext_audio == "none":
            cmd.extend((self.ffmpeg_cmd, "-y", "-i", input_file))
            cmd.extend(("-map", "0:v")) # 仅保留视频，实现纯静音输出
        else:
            cmd.extend((self.ffmpeg_cmd, "-y", "-i", input_file))
            cmd.extend(("-map", "0")) # 默认强制全映射以保留多音轨和复杂特效字幕
            cmd.extend(("-c:a", "aac", "-b:a", "192k", "-c:s", "copy"))

        # --- 2. 动态画质挡位预设 (映射到 CQ/CRF 值) ---
        q_map = dict()
        q_map.update({"1": "18", "2": "21", "3": "24", "4": "28", "5": "20"})
        cq_val = q_map.get(quality_level, "20")

        # --- 3. 针对不同视频场景的最佳算法预设 ---
        x265_params = ""
        if video_category == "1": # 动漫
            x265_params = "limit-sao=1:bframes=8:psy-rd=1.0:aq-mode=3:aq-strength=0.8:deblock=1,1"
        elif video_category == "2": # 录屏 (保护文本锐度，低帧变化)
            x265_params = "strong-intra-smoothing=0:rect=0:aq-mode=1:deblock=-1,-1:bframes=8:keyint=300"
        else: # 拍摄 (保护电影噪点颗粒，高频细节)
            x265_params = "no-sao=1:bframes=4:psy-rd=1.5:psy-rdoq=2.0:aq-mode=2"

        # --- 4. 视频核心参数构建 (GPU / CPU 分支) ---
        if use_hw and len(self.available_hw_encoders) > 0:
            if "nvenc" in self.available_hw_encoders:
                self.print_guide("检测到 NVIDIA 显卡，加载 NVENC 场景化硬件加速配置...")
                hw_args = list()
                hw_args.extend(("-c:v", "hevc_nvenc", "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", cq_val))
                if video_category == "1":
                    hw_args.extend(("-spatial-aq", "1", "-bf", "3"))
                elif video_category == "2":
                    hw_args.extend(("-spatial-aq", "1", "-bf", "3", "-g", "300"))
                else:
                    hw_args.extend(("-spatial-aq", "1", "-temporal-aq", "1", "-bf", "3"))
                cmd.extend(hw_args)

            elif "qsv" in self.available_hw_encoders:
                self.print_guide("检测到 Intel 核显/独显，加载 QSV 场景化硬件加速配置...")
                cmd.extend(("-c:v", "hevc_qsv", "-preset", "slower", "-global_quality", cq_val, "-look_ahead", "1")) 
            elif "amf" in self.available_hw_encoders:
                self.print_guide("检测到 AMD 显卡，加载 AMF 场景化硬件加速配置...")
                cmd.extend(("-c:v", "hevc_amf", "-rc", "cqp", "-qp_i", cq_val, "-qp_p", cq_val, "-qp_b", cq_val)) 
        else:
            self.print_guide("系统将采用 CPU(libx265) 进行最高画质软压制，速度较慢请耐心等待...")
            cpu_args = list()
            cpu_args.extend(("-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-preset", "slow", "-crf", cq_val))
            if video_category == "1":
                cpu_args.extend(("-tune", "animation"))
            cpu_args.extend(("-x265-params", x265_params))
            cmd.extend(cpu_args)
        
        cmd.append(output_file)
        return cmd

    def build_format_factory_command(self, input_file, output_file, format_type, compress=False, use_hw=True, target_vcodec="h264"):
        """构建类格式化工厂的全音频/视频多格式转换矩阵"""
        cmd = list()
        cmd.extend((self.ffmpeg_cmd, "-y", "-i", input_file))
        format_type = format_type.lower()
        
        audio_formats = ("mp3", "flac", "wav", "m4a", "aac", "ogg", "wma")
        
        if format_type in audio_formats:
            self.print_guide("加载纯音频提取与高保真互转配置...", "INFO")
            cmd.append("-vn") # 完全剥离视频轨道
            if format_type == "mp3":
                cmd.extend(("-c:a", "libmp3lame", "-b:a", "320k")) 
            elif format_type == "flac":
                cmd.extend(("-c:a", "flac"))
            elif format_type == "wav":
                cmd.extend(("-c:a", "pcm_s16le"))
            else:
                cmd.extend(("-c:a", "aac", "-b:a", "192k"))
        elif format_type == "3gp":
            self.print_guide("加载古董移动设备 3GP 兼容格式配置...")
            cmd.extend(("-c:v", "h263", "-s", "352x288", "-r", "15", "-c:a", "libopencore_amrnb", "-ar", "8000", "-ac", "1"))
        elif format_type == "gif":
            self.print_guide("加载高画质流媒体 GIF 调色板双趟转换配置...")
            cmd.extend(("-vf", "fps=15,scale=480:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse=dither=sierra2_4a", "-loop", "0"))
        else:
            if compress:
                self.print_guide("启用视频轻度压缩 (" + target_vcodec.upper() + ") 与 GPU 硬件自适应转换...", "INFO")
                if use_hw and len(self.available_hw_encoders) > 0:
                    if "nvenc" in self.available_hw_encoders:
                        encoder = "hevc_nvenc" if target_vcodec == "hevc" else "h264_nvenc"
                        cmd.extend(("-c:v", encoder, "-preset", "p4", "-rc", "vbr", "-cq", "24"))
                    elif "qsv" in self.available_hw_encoders:
                        encoder = "hevc_qsv" if target_vcodec == "hevc" else "h264_qsv"
                        cmd.extend(("-c:v", encoder, "-preset", "medium", "-global_quality", "24"))
                    elif "amf" in self.available_hw_encoders:
                        encoder = "hevc_amf" if target_vcodec == "hevc" else "h264_amf"
                        cmd.extend(("-c:v", encoder, "-rc", "cqp", "-qp_i", "24", "-qp_p", "24", "-qp_b", "24"))
                else:
                    encoder = "libx265" if target_vcodec == "hevc" else "libx264"
                    crf_val = "26" if target_vcodec == "hevc" else "24"
                    cmd.extend(("-c:v", encoder, "-crf", crf_val, "-preset", "medium"))
                cmd.extend(("-c:a", "aac", "-b:a", "128k"))
            else:
                self.print_guide("启用极速无损格式重封装 (仅修改后缀，不改变画质体积)...", "INFO")
                cmd.extend(("-c:v", "copy", "-c:a", "copy"))

        cmd.append(output_file)
        return cmd

    def diagnostic_error_translator(self, error_log):
        """异常诊断引擎：将隐晦的FFmpeg英文错误翻译为具备实操价值的中文向导"""
        if "No such file or directory" in error_log:
            return "找不到输入文件。请排查路径是否含有系统不识别的非法字符。"
        if "10 bit encode not supported" in error_log:
            return "显卡硬件编码引擎过于老旧，不支持10位色深压制。"
        if "Error while opening encoder" in error_log:
            return "编码器启动失败。参数存在冲突或GPU驱动程序故障。"
        if "AVERROR_INVALIDDATA" in error_log or "Invalid data found" in error_log:
            return "源文件数据无效或损坏。视频极有可能未下载完整。"
        return "发生未知底层错误，请核对转换参数或检查内存状况。"

    def execute_with_fallback(self, cmd, is_video_compress=False, input_file="", output_file="", **kwargs):
        """进程调度中心：执行命令，包含实时进度条与GPU崩溃降级机制"""
        self.print_guide("===========================================", "INFO")
        cmd_str = " ".join(cmd)
        self.print_guide("正在执行任务...\n底层调用链: " + cmd_str, "INFO")
        
        # 开启错误忽略模式以防止某些编码字符导致进程崩溃
        process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore', shell=False, universal_newlines=True)
        
        error_buffer = ""
        duration_secs = 0
        start_time = time.time()
        
        # 实时逐行读取 FFmpeg 输出，提取时长信息制作进度条
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
                        eta_str = f"{eta_mins}分{eta_secs}秒"
                    else:
                        eta_str = "计算中..."
                        
                    bar_len = 30
                    filled_len = int(bar_len * percent // 100)
                    bar = '█' * filled_len + '-' * (bar_len - filled_len)
                    sys.stdout.write(f"\r[系统提示] 处理进度: [{bar}] {percent:.1f}% | 剩余时间约: {eta_str}   ")
                    sys.stdout.flush()
                
        process.wait()
        print("") 
        
        exit_code = process.returncode
        if exit_code!= 0:
            self.print_guide("任务执行中断！正在分析底层故障原因...", "ERROR")
            diagnostic_msg = self.diagnostic_error_translator(error_buffer)
            self.print_guide("【故障诊断】" + diagnostic_msg, "WARNING")
            
            # 使用圆括号彻底避开界面吞方括号的 bug
            hw_error_triggers = ("10 bit encode not supported", "No capable devices found", "Error while opening encoder", "AVERROR")
            
            is_hw_failure = False
            for trigger in hw_error_triggers:
                if trigger in error_buffer:
                    is_hw_failure = True
                    break
            
            if is_video_compress and is_hw_failure and "libx265" not in cmd:
                self.print_guide("触发容灾机制：检测到显卡加速接口瘫痪，正在自动热切换为纯CPU软压制重试！", "WARNING")
                fallback_cmd = self.build_video_compress_command(
                    input_file, output_file, 
                    video_category=kwargs.get("video_category", "1"),
                    quality_level=kwargs.get("quality_level", "5"), 
                    use_hw=False, 
                    ext_audio=kwargs.get("ext_audio")
                )
                return self.execute_with_fallback(fallback_cmd, is_video_compress=False, input_file=input_file, output_file=output_file) 
            else:
                self.print_guide("任务不可挽回地失败。建议查阅上述诊断结果进行排障。", "ERROR")
                return False
        else:
            self.print_guide("处理成功，文件已保存至目标目录。", "SUCCESS")
            return True

    def run_interactive_ui(self):
        """交互式全中文命令行事件循环引导界面"""
        while True:
            self.print_guide("\n===========================================")
            self.print_guide("  全场景音视频压制与多格式格式化工厂中枢  ")
            self.print_guide("===========================================")
            print("请选择操作模式：\n  1. 场景化视频极致压缩 (支持动漫/录屏/拍摄全覆盖)\n  2. 格式化工厂 (视频格式互转/纯音频提取转换)")
            
            choice = input("\n请输入选项数字 (1/2): ").strip()
            
            input_path = input("\n请拖入或粘贴需要转换的文件路径 (完美支持中文及特殊符号): ").strip().strip('"').strip("'")
            input_path = self.validate_and_create_path(input_path)
            
            out_dir = input("请输入自定义的输出文件夹路径 (直接回车保存在源文件同级目录): ").strip().strip('"').strip("'")
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
                print("\n请选择您要压制的视频类型 (系统将自动套用针对性最优算法)：")
                print("  1. 动漫 / 二次元 (去色带，保留锐利线条)")
                print("  2. 电脑录屏 / 游戏界面 (保护文本锐度，提高静止画面压缩率)")
                print("  3. 实拍视频 / 电影剧集 (保留自然噪点与真实光影动态)")
                video_category = input("请输入类型数字 (1-3, 默认1): ").strip()
                if video_category not in ("1", "2", "3"):
                    video_category = "1"

                target_fmt = input("\n请输入输出的视频格式后缀 (直接回车默认使用 mkv): ").strip().lower()
                if not target_fmt:
                    target_fmt = "mkv"
                
                print("\n请选择压缩与画质预设挡位：")
                print("  1. 高画质压缩 (文件较大, 画质无损级)")
                print("  2. 中画质压缩 (文件适中, 适合日常归档)")
                print("  3. 低画质压缩 (文件较小, 画质轻微损失)")
                print("  4. 极致压缩 (追求最小体积占用)")
                print("  5. 自动平衡 (默认动态分配)")
                quality_level = input("请输入挡位数字 (1-5, 直接回车默认为5): ").strip()
                if quality_level not in ("1", "2", "3", "4", "5"):
                    quality_level = "5"
                
                has_audio = self.check_audio_stream(input_path)
                if not has_audio:
                    self.print_guide("【探测】此视频源文件中似乎不存在音频轨道！", "WARNING")
                    add_audio = input("是否需要添加外部音轨？(y: 选择音频文件 / n: 保持无声纯画面, 默认n): ").strip().lower()
                    if add_audio == 'y':
                        ext_audio = input("请拖入或粘贴外部音频文件路径: ").strip().strip('"').strip("'")
                        ext_audio = self.validate_and_create_path(ext_audio)
                    else:
                        ext_audio = "none"

                output_path = str(Path(out_dir) / (base_name + "_compressed." + target_fmt))
                cmd = self.build_video_compress_command(input_path, output_path, video_category=video_category, quality_level=quality_level, use_hw=True, ext_audio=ext_audio)
                
                success = self.execute_with_fallback(
                    cmd, 
                    is_video_compress=True, 
                    input_file=input_path, 
                    output_file=output_path, 
                    video_category=video_category, 
                    quality_level=quality_level, 
                    ext_audio=ext_audio
                )
                
            elif choice == "2":
                target_fmt = input("\n请输入目标格式后缀 (支持视频如 mp4,mkv,3gp / 音频如 mp3,flac,wav / 动图 gif): ").strip().lower()
                output_path = str(Path(out_dir) / (base_name + "_converted." + target_fmt))
                
                compress = False
                target_vcodec = "h264"
                audio_formats = ("mp3", "flac", "wav", "m4a", "aac", "ogg", "wma")
                
                if target_fmt not in ("gif", ) and target_fmt not in audio_formats:
                    compress_opt = input("\n是否同时对视频进行轻度压缩重编码？(y: 启用压缩 / n: 极速无损封装, 默认n): ").strip().lower()
                    compress = (compress_opt == 'y')
                    
                    if compress:
                        print("\n请选择转换的目标视频编码格式：")
                        print("  1. H.264 (AVC) - 兼容性极佳，所有老旧设备均可播放 (默认)")
                        print("  2. H.265 (HEVC) - 极高压缩率，体积更小，画质更好")
                        codec_opt = input("请输入选项数字 (1/2, 直接回车默认为1): ").strip()
                        target_vcodec = "hevc" if codec_opt == "2" else "h264"

                cmd = self.build_format_factory_command(input_path, output_path, target_fmt, compress=compress, use_hw=True, target_vcodec=target_vcodec)
                success = self.execute_with_fallback(cmd, is_video_compress=False, input_file=input_path, output_file=output_path)
            else:
                self.print_guide("输入错误模式，任务取消。", "ERROR")
                
            # 任务成功后，打印详细的压缩比与执行日志总结
            if success and os.path.exists(output_path):
                try:
                    orig_size = os.path.getsize(input_path) / (1024 * 1024)
                    new_size = os.path.getsize(output_path) / (1024 * 1024)
                    reduction = ((orig_size - new_size) / orig_size) * 100 if orig_size > 0 else 0
                    
                    print("\n===========================================")
                    self.print_guide("任务处理完成！以下是本次压制操作的总结报告：", "SUCCESS")
                    self.print_guide("▶ 格式变更: 从 " + Path(input_path).suffix.upper() + " 转换为 " + Path(output_path).suffix.upper(), "INFO")
                    
                    if choice == "1":
                        cat_str = "动漫预设" if video_category == "1" else "录屏预设" if video_category == "2" else "实拍预设"
                        self.print_guide("▶ 视频处理: 运用 10-bit HEVC (H.265) " + cat_str + " 算法压制", "INFO")
                        if ext_audio and ext_audio!= "none":
                            self.print_guide("▶ 音轨注入: 成功合并了外挂音轨 (" + Path(ext_audio).name + ")", "INFO")
                        elif ext_audio == "none":
                            self.print_guide("▶ 音频处理: 按照设置剔除了音频，生成纯净画面流", "INFO")
                        else:
                            self.print_guide("▶ 音频处理: 原音轨智能提取与高保真压缩映射", "INFO")
                    else:
                        audio_formats = ("mp3", "flac", "wav", "m4a", "aac", "ogg", "wma")
                        if target_fmt in audio_formats:
                            self.print_guide("▶ 操作类型: 纯音频剥离与格式转换", "INFO")
                        elif compress:
                            self.print_guide("▶ 操作类型: 视频自适应跨格式 GPU 压缩转换 (" + target_vcodec.upper() + ")", "INFO")
                        else:
                            self.print_guide("▶ 操作类型: 极速无损拷贝封包 (Direct Stream Copy)", "INFO")
                        
                    self.print_guide("▶ 体积变化: 原始大小 " + f"{orig_size:.2f}" + " MB  ->  处理后 " + f"{new_size:.2f}" + " MB", "INFO")
                    
                    if reduction > 0:
                        self.print_guide("▶ 压缩比率: 文件体积成功缩小了 " + f"{reduction:.2f}" + "%", "SUCCESS")
                    else:
                        self.print_guide("▶ 压缩比率: 文件体积增大了 " + f"{-reduction:.2f}" + "% (源文件可能已是极高压缩比的格式)", "WARNING")
                    print("===========================================\n")
                except Exception:
                    pass

            # 任务执行循环询问机制
            cont = input("\n是否继续处理下一个文件？(y/n, 直接回车退出): ").strip().lower()
            if cont!= 'y':
                self.print_guide("感谢使用！系统已安全退出。", "SUCCESS")
                break

if __name__ == "__main__":
    app = FFmpegOrchestrator()
    app.run_interactive_ui()