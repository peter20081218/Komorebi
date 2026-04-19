import os
import threading
import customtkinter as ctk
from tkinter import filedialog
from pathlib import Path
import sys
import re

try:
    from Komorebi import FFmpegOrchestrator
except ImportError:
    print("【系统错误】找不到 Komorebi.py，请确保 GUI 脚本与之放在同一目录下！")

ctk.set_appearance_mode("Dark")  
ctk.set_default_color_theme("blue")  

class RedirectStdout:
    def __init__(self, text_widget, progress_bar, status_label):
        self.text_widget = text_widget
        self.progress_bar = progress_bar
        self.status_label = status_label

    def write(self, string):
        if "\r" in string and ("进度" in string or "%" in string or "全体总进度" in string):
            try:
                match_pct = re.search(r"\]\s*([\d\.]+)%", string)
                match_eta = re.search(r"剩余约:\s*([^\s]+)", string)
                
                if match_pct:
                    pct = float(match_pct.group(1)) / 100.0
                    self.text_widget.after(0, self.progress_bar.set, pct)
                if match_eta:
                    eta = match_eta.group(1)
                    self.text_widget.after(0, self.status_label.configure, text=f"任务执行中... 剩余约 {eta}")
            except Exception:
                pass
        elif "\r" not in string and string.strip():
            self.text_widget.after(0, self._insert_text, string + "\n")

    def _insert_text(self, text):
        self.text_widget.insert("end", text)
        self.text_widget.see("end")

    def flush(self):
        pass

class KomorebiApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.orchestrator = FFmpegOrchestrator()
        self.current_mode = "1" 
        self.current_media_info = {} 
        self.is_batch_mode = False   
        self.is_task_running = False

        self.title("Komorebi 多媒体智能引擎 - 终极版")
        self.geometry("950x800")
        self.minsize(850, 780)

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # ==================== 左侧导航栏 ====================
        self.sidebar_frame = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(4, weight=1) 

        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="Komorebi\n自动化中枢", font=ctk.CTkFont(size=22, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 30))

        self.btn_mode_compress = ctk.CTkButton(self.sidebar_frame, text="场景化视频极致压缩", command=self.show_compress_mode)
        self.btn_mode_compress.grid(row=1, column=0, padx=20, pady=10)

        self.btn_mode_format = ctk.CTkButton(self.sidebar_frame, fg_color="transparent", border_width=2, text="全能格式化工厂", command=self.show_format_mode)
        self.btn_mode_format.grid(row=2, column=0, padx=20, pady=10)

        # ==================== 右侧主内容区 ====================
        self.main_scroll = ctk.CTkScrollableFrame(self, corner_radius=10)
        self.main_scroll.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")
        self.main_scroll.grid_columnconfigure(1, weight=1)

        # --- 1. 路径选择区 ---
        self.path_frame = ctk.CTkLabel(self.main_scroll, text="📁 路径与文件管理", font=ctk.CTkFont(size=15, weight="bold"))
        self.path_frame.grid(row=0, column=0, columnspan=4, padx=20, pady=(10, 5), sticky="w")

        self.lbl_in = ctk.CTkLabel(self.main_scroll, text="输入源路径:")
        self.lbl_in.grid(row=1, column=0, padx=20, pady=5, sticky="e")
        self.entry_input = ctk.CTkEntry(self.main_scroll, placeholder_text="支持拖入或选择文件/目录...")
        self.entry_input.grid(row=1, column=1, padx=(0, 10), pady=5, sticky="ew")
        self.btn_in_file = ctk.CTkButton(self.main_scroll, text="选文件", width=50, command=lambda: self.browse_input(is_dir=False))
        self.btn_in_file.grid(row=1, column=2, padx=(0, 5), pady=5)
        self.btn_in_dir = ctk.CTkButton(self.main_scroll, text="选目录", width=50, fg_color="#4a4a4a", hover_color="#333333", command=lambda: self.browse_input(is_dir=True))
        self.btn_in_dir.grid(row=1, column=3, padx=(0, 20), pady=5)

        self.lbl_out, self.entry_output, self.btn_out = self.create_path_row("输出存放目录:", 2, self.browse_output, "留空则保存在源文件同级目录...")
        self.lbl_rename, self.entry_rename, _ = self.create_path_row("输出重命名:", 3, None, "选填 (仅单文件有效，无需写后缀)")
        self.lbl_aud, self.entry_audio, self.btn_aud = self.create_path_row("混入外部音频:", 4, self.browse_audio, "检测到源视频无声，可选择混入音轨...")
        self.hide_audio_row()

        # --- 2. 动态参数设置区 ---
        self.param_title = ctk.CTkLabel(self.main_scroll, text="⚙️ 核心处理参数", font=ctk.CTkFont(size=15, weight="bold"))
        self.param_title.grid(row=5, column=0, columnspan=4, padx=20, pady=(20, 5), sticky="w")

        self.param_container = ctk.CTkFrame(self.main_scroll, fg_color="transparent")
        self.param_container.grid(row=6, column=0, columnspan=4, sticky="ew", padx=10)
        self.param_container.grid_columnconfigure((1, 3), weight=1)

        self.ui_compress = self.setup_compress_ui()
        self.ui_format = self.setup_format_ui()
        self.show_compress_mode()

        # --- 3. 性能面板 ---
        self.perf_title = ctk.CTkLabel(self.main_scroll, text="🚀 算力并发与性能分配 (批量模式激活)", font=ctk.CTkFont(size=15, weight="bold"))
        self.perf_frame = ctk.CTkFrame(self.main_scroll)
        self.perf_frame.grid_columnconfigure((1, 3), weight=1)
        self.label_workers = ctk.CTkLabel(self.perf_frame, text="并发任务数:")
        self.label_workers.grid(row=0, column=0, padx=10, pady=10)
        self.entry_workers = ctk.CTkEntry(self.perf_frame, width=60)
        self.entry_workers.insert(0, "2")
        self.entry_workers.grid(row=0, column=1, padx=10, pady=10, sticky="w")
        self.label_threads = ctk.CTkLabel(self.perf_frame, text="单任务线程限制:")
        self.label_threads.grid(row=0, column=2, padx=10, pady=10)
        self.entry_threads = ctk.CTkEntry(self.perf_frame, width=60)
        self.entry_threads.insert(0, "0") 
        self.entry_threads.grid(row=0, column=3, padx=10, pady=10, sticky="w")
        self.switch_batch_all = ctk.CTkSwitch(self.perf_frame, text="对目录下所有文件应用全局设置", command=self.toggle_batch_list)
        self.switch_batch_all.select()
        self.switch_batch_all.grid(row=1, column=0, columnspan=2, padx=20, pady=10, sticky="w")
        
        # --- 新增：独立设置列表容器（默认隐藏） ---
        self.batch_list_frame = ctk.CTkFrame(self.perf_frame, fg_color="transparent")
        self.file_setting_widgets = {}

        self.hide_perf_row()

        # --- 4. 终端日志 ---
        self.textbox_log = ctk.CTkTextbox(self.main_scroll, height=180)
        self.textbox_log.grid(row=9, column=0, columnspan=4, padx=20, pady=20, sticky="nsew")

        # --- 底部控制面板 ---
        self.lbl_status = ctk.CTkLabel(self, text="系统空闲中", font=ctk.CTkFont(weight="bold"))
        self.lbl_status.grid(row=1, column=1, padx=20, pady=(10, 0), sticky="w")

        self.progressbar = ctk.CTkProgressBar(self)
        self.progressbar.grid(row=2, column=1, padx=20, pady=(5, 10), sticky="ew")
        self.progressbar.set(0)

        # 双按钮布局控制
        self.control_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.control_frame.grid(row=3, column=1, padx=20, pady=(0, 20), sticky="ew")
        self.control_frame.grid_columnconfigure((0, 1), weight=1)

        self.btn_start = ctk.CTkButton(self.control_frame, text="🚀 启动核心引擎", font=ctk.CTkFont(size=16, weight="bold"), height=45, command=self.start_task)
        self.btn_start.grid(row=0, column=0, padx=(0, 10), sticky="ew")

        self.btn_stop = ctk.CTkButton(self.control_frame, text="⏹ 终止所有任务", font=ctk.CTkFont(size=16, weight="bold"), height=45, fg_color="#c0392b", hover_color="#922b21", state="disabled", command=self.stop_task)
        self.btn_stop.grid(row=0, column=1, padx=(10, 0), sticky="ew")

        sys.stdout = RedirectStdout(self.textbox_log, self.progressbar, self.lbl_status)

        print("(系统提示) Komorebi 引擎已就绪。木漏れ日：为您平衡画质保真、微小体积与处理速度。")

    # ==================== 智能 UI 动态反射核心 ====================

    def on_compress_format_change(self, choice):
        if choice == "webm":
            print("\n【GUI 智能修正】WebM 容器规范严格禁止 H.264/HEVC，已为您强制锁定 AV1 编码以防底层报错！")
            self.combo_codec.set("3. AV1 (极限)")
            self.combo_codec.configure(state="disabled") 
        else:
            self.combo_codec.configure(state="normal") 
        self.update_quality_estimates()

    def on_format_target_change(self, choice):
        audio_formats = ["mp3", "flac", "wav", "aac", "m4a", "ogg", "wma", "opus", "ac3"]
        lossless_formats = ["flac", "wav"]
        
        # 1. 视频编码器隔离
        if choice in audio_formats:
            self.combo_fmt_vcodec.set("- 纯音频无需视频 -")
            self.combo_fmt_vcodec.configure(state="disabled")
        else:
            self.combo_fmt_vcodec.configure(state="normal")
            self.combo_fmt_vcodec.set("1. H.264")
            
        # 2. 无损音频体积隔离 (修复 FLAC 选挡位无效的逻辑疑惑)
        if choice in lossless_formats:
            self.combo_fmt_quality.set("- 无损格式体积恒定 -")
            self.combo_fmt_quality.configure(state="disabled")
        else:
            self.combo_fmt_quality.configure(state="normal")
            self.combo_fmt_quality.set("n. 极速直通(不转码)")
            
        # 联动触发格式化工厂的体积预估
        self.update_format_estimates()
    def update_format_estimates(self, *args):
        # 修复：为格式化工厂补全预估功能
        if not self.current_media_info or self.is_batch_mode:
            self.combo_fmt_quality.configure(values=["n. 极速直通(不转码)", "1. 高保真转码", "2. 标准转码", "3. 低码率压缩"])
            return

        target_fmt = self.combo_fmt_target.get()
        orig_dur = self.current_media_info.get("duration", 0)
        audio_formats = ["mp3", "aac", "m4a", "ogg", "wma", "opus", "ac3"]
        
        if target_fmt in audio_formats:
            a1 = self.orchestrator._estimate_audio_size(orig_dur, 320)
            a2 = self.orchestrator._estimate_audio_size(orig_dur, 192)
            a3 = self.orchestrator._estimate_audio_size(orig_dur, 128)
            a4 = self.orchestrator._estimate_audio_size(orig_dur, 64)
            vals = ["n. 极速直通(不转码)", f"1. 极高音质 (~{a1} MB)", f"2. 高保真 (~{a2} MB)", f"3. 流媒体 (~{a3} MB)", f"4. 极致压缩 (~{a4} MB)"]
            self.combo_fmt_quality.configure(values=vals)
        elif target_fmt not in ["flac", "wav", "gif"]:
            orig_mb = self.current_media_info.get("size_mb", 0)
            orig_w, orig_h = self.current_media_info.get("width", 1920), self.current_media_info.get("height", 1080)
            vcodec = {"1": "h264", "2": "hevc", "3": "av1"}.get(self.combo_fmt_vcodec.get()[0], "h264") if "无需视频" not in self.combo_fmt_vcodec.get() else "h264"
            use_hw = len(self.orchestrator.available_hw_encoders) > 0
            s1, _ = self.orchestrator._estimate_size_and_loss(orig_mb, orig_dur, orig_w, orig_h, self.current_media_info.get("v_codec", ""), vcodec, "1", use_hw)
            s2, _ = self.orchestrator._estimate_size_and_loss(orig_mb, orig_dur, orig_w, orig_h, self.current_media_info.get("v_codec", ""), vcodec, "2", use_hw)
            s3, _ = self.orchestrator._estimate_size_and_loss(orig_mb, orig_dur, orig_w, orig_h, self.current_media_info.get("v_codec", ""), vcodec, "3", use_hw)
            vals = ["n. 极速直通(不转码)", f"1. 高保真转码 (~{s1} MB)", f"2. 标准转码 (~{s2} MB)", f"3. 低码率压缩 (~{s3} MB)"]
            self.combo_fmt_quality.configure(values=vals)
    def update_quality_estimates(self, *args):
        # 修复：如果没有选择文件，只显示基础文字，不显示预估体积！
        if not self.current_media_info or self.is_batch_mode:
            self.combo_quality.configure(values=["1. 极致高保真", "2. 优质归档", "3. 日常流媒体", "4. 极限存储"])
            self.combo_quality.set("2. 优质归档")
            return
            
        orig_mb = self.current_media_info.get("size_mb", 0)
        orig_dur = self.current_media_info.get("duration", 0)
        orig_w = self.current_media_info.get("width", 1920)
        orig_h = self.current_media_info.get("height", 1080)
        src_codec = self.current_media_info.get("v_codec", "")
        codec_str = self.combo_codec.get()
        vcodec = {"1": "h264", "2": "hevc", "3": "av1"}.get(codec_str[0], "hevc") if "AV1" not in codec_str else "av1"
        use_hw = len(self.orchestrator.available_hw_encoders) > 0

        s1, _ = self.orchestrator._estimate_size_and_loss(orig_mb, orig_dur, orig_w, orig_h, src_codec, vcodec, "1", use_hw)
        s2, _ = self.orchestrator._estimate_size_and_loss(orig_mb, orig_dur, orig_w, orig_h, src_codec, vcodec, "2", use_hw)
        s3, _ = self.orchestrator._estimate_size_and_loss(orig_mb, orig_dur, orig_w, orig_h, src_codec, vcodec, "3", use_hw)
        s4, _ = self.orchestrator._estimate_size_and_loss(orig_mb, orig_dur, orig_w, orig_h, src_codec, vcodec, "4", use_hw)

        vals = [f"1. 极致高保真 (预估: ~{s1} MB)", f"2. 优质归档 (预估: ~{s2} MB)", f"3. 日常流媒体 (预估: ~{s3} MB)", f"4. 极限存储 (预估: ~{s4} MB)"]
        self.combo_quality.configure(values=vals)
        try:
            current_idx = int(self.combo_quality.get()[0]) - 1 if self.combo_quality.get()[0].isdigit() else 1
            self.combo_quality.set(vals[current_idx])
        except: pass
    # ==================== UI 构建 ====================
    def setup_compress_ui(self):
        f = ctk.CTkFrame(self.param_container, fg_color="transparent")
        f.grid_columnconfigure((1, 3), weight=1)
        self.combo_category = self.create_combo(f, "场景算法:", ["1. 动漫/二次元", "2. 电脑录屏/游戏", "3. 实拍视频/电影"], 0, 0)
        self.combo_codec = self.create_combo(f, "编码内核:", ["2. H.265 (推荐)", "1. H.264 (兼容)", "3. AV1 (极限)"], 0, 2, command=self.update_quality_estimates)
        self.combo_quality = self.create_combo(f, "质量等级:", ["1. 极致高保真", "2. 优质归档", "3. 日常流媒体", "4. 极限存储"], 1, 0)
        self.combo_format = self.create_combo(f, "封装格式:", ["mkv", "mp4", "webm"], 1, 2, command=self.on_compress_format_change)
        self.combo_quality.set("2. 优质归档")
        return f

    def setup_format_ui(self):
        f = ctk.CTkFrame(self.param_container, fg_color="transparent")
        f.grid_columnconfigure((1, 3), weight=1)
        self.combo_fmt_target = self.create_combo(f, "目标格式:", ["mp4", "mkv", "mp3", "flac", "gif", "webm", "wav"], 0, 0, command=self.on_format_target_change)
        # 注意下面这行新增了 command=self.update_format_estimates
        self.combo_fmt_quality = self.create_combo(f, "压缩挡位:", ["n. 极速直通(不转码)", "1. 高保真转码", "2. 标准转码", "3. 低码率压缩"], 0, 2)
        self.combo_fmt_vcodec = self.create_combo(f, "指定视频编码:", ["1. H.264", "2. H.265", "3. AV1"], 1, 0, command=self.update_format_estimates)
        self.combo_fmt_quality.set("n. 极速直通(不转码)")
        return f

    def create_combo(self, parent, label, values, row, col, command=None):
        ctk.CTkLabel(parent, text=label).grid(row=row, column=col, padx=10, pady=10, sticky="e")
        combo = ctk.CTkComboBox(parent, values=values, command=command)
        combo.grid(row=row, column=col+1, padx=10, pady=10, sticky="ew")
        return combo

    # ==================== 路径与模式切换 ====================
    def create_path_row(self, label_text, row, command, placeholder=""):
        lbl = ctk.CTkLabel(self.main_scroll, text=label_text)
        lbl.grid(row=row, column=0, padx=20, pady=5, sticky="e")
        entry = ctk.CTkEntry(self.main_scroll, placeholder_text=placeholder)
        entry.grid(row=row, column=1, columnspan=2 if not command else 1, padx=(0, 10) if command else (0, 20), pady=5, sticky="ew")
        btn = None
        if command:
            btn = ctk.CTkButton(self.main_scroll, text="选择", width=50, command=command)
            btn.grid(row=row, column=2, columnspan=2, padx=(0, 20), pady=5, sticky="w")
        return lbl, entry, btn

    def hide_audio_row(self):
        self.lbl_aud.grid_remove(); self.entry_audio.grid_remove(); self.btn_aud.grid_remove()
    def show_audio_row(self):
        self.lbl_aud.grid(); self.entry_audio.grid(); self.btn_aud.grid()
    def hide_perf_row(self):
        self.perf_title.grid_remove(); self.perf_frame.grid_remove()
        self.lbl_rename.grid(); self.entry_rename.grid() 
    def show_perf_row(self):
        self.perf_title.grid(row=7, column=0, columnspan=4, padx=20, pady=(20, 5), sticky="w")
        self.perf_frame.grid(row=8, column=0, columnspan=4, padx=20, pady=10, sticky="ew")
        self.lbl_rename.grid_remove(); self.entry_rename.grid_remove()
    def toggle_batch_list(self):
        """控制多文件独立设置列表的展开与折叠"""
        if self.switch_batch_all.get() == 1:
            self.batch_list_frame.grid_remove()
        else:
            # 【修复】增加 sticky="nsew" 强制撑开折叠的框架
            self.batch_list_frame.grid(row=2, column=0, columnspan=4, padx=10, pady=10, sticky="nsew")
            self.populate_batch_list()

    def populate_batch_list(self):
        """渲染目录下的所有文件，并赋予独立的下拉菜单"""
        for widget in self.batch_list_frame.winfo_children():
            widget.destroy()
        self.file_setting_widgets.clear()
        
        input_path = self.entry_input.get()
        if not input_path or not os.path.isdir(input_path): return
        
        valid_exts = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".3gp", ".ts", ".m2ts", ".vob", ".rmvb", ".ogv", ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".wma", ".opus", ".ac3")
        files = [f for f in Path(input_path).iterdir() if f.is_file() and f.suffix.lower() in valid_exts]
        
        # 【新增：终极防呆】如果没有找到支持的文件，显示红色警告，而不是诡异的空白
        if not files:
            lbl = ctk.CTkLabel(self.batch_list_frame, text="⚠️ 暂无数据: 当前目录未直接包含支持的媒体文件\n(提示: BDMV蓝光原盘请直接选择内部的 STREAM 文件夹)", text_color="#e74c3c")
            lbl.grid(row=0, column=0, padx=10, pady=20)
            return
        
        for i, f in enumerate(files):
            # 【修复】压缩文件名显示长度，防止超长文件名撑爆 UI 导致整个组件消失
            lbl = ctk.CTkLabel(self.batch_list_frame, text=f.name[:15] + ("..." if len(f.name)>15 else ""))
            lbl.grid(row=i, column=0, padx=5, pady=5, sticky="w")
            
            if self.current_mode == "1":
                c_codec = ctk.CTkComboBox(self.batch_list_frame, values=["2. H.265", "1. H.264", "3. AV1"], width=85)
                c_codec.set("2. H.265")
                c_codec.grid(row=i, column=1, padx=2, pady=5)
                c_qual = ctk.CTkComboBox(self.batch_list_frame, values=["1. 极致保真", "2. 优质归档", "3. 流媒体", "4. 极限存储"], width=95)
                c_qual.set("2. 优质归档")
                c_qual.grid(row=i, column=2, padx=2, pady=5)
                c_fmt = ctk.CTkComboBox(self.batch_list_frame, values=["mkv", "mp4", "webm"], width=65)
                c_fmt.set("mkv")
                c_fmt.grid(row=i, column=3, padx=2, pady=5)
                self.file_setting_widgets[str(f)] = {"codec": c_codec, "quality": c_qual, "format": c_fmt}
            else:
                c_fmt = ctk.CTkComboBox(self.batch_list_frame, values=["mp4", "mkv", "mp3", "flac", "gif", "webm", "wav"], width=65)
                c_fmt.set("mp4")
                c_fmt.grid(row=i, column=1, padx=2, pady=5)
                c_codec = ctk.CTkComboBox(self.batch_list_frame, values=["1. H.264", "2. H.265", "3. AV1"], width=85)
                c_codec.set("1. H.264")
                c_codec.grid(row=i, column=2, padx=2, pady=5)
                c_qual = ctk.CTkComboBox(self.batch_list_frame, values=["n. 直通(不转码)", "1. 高保真", "2. 标准转码", "3. 低码率"], width=105)
                c_qual.set("n. 直通(不转码)")
                c_qual.grid(row=i, column=3, padx=2, pady=5)
                self.file_setting_widgets[str(f)] = {"format": c_fmt, "codec": c_codec, "quality": c_qual}
                
        self.batch_list_frame.grid_columnconfigure(0, weight=1)
    def browse_input(self, is_dir=False):
        p = filedialog.askdirectory() if is_dir else filedialog.askopenfilename()
        if p: 
            self.entry_input.delete(0, 'end')
            self.entry_input.insert(0, p)
            self.analyze_selected_path(p)

    def browse_output(self):
        p = filedialog.askdirectory()
        if p: self.entry_output.delete(0, 'end'); self.entry_output.insert(0, p)
    def browse_audio(self):
        p = filedialog.askopenfilename()
        if p: self.entry_audio.delete(0, 'end'); self.entry_audio.insert(0, p)

    def show_compress_mode(self):
        self.current_mode = "1"
        self.btn_mode_compress.configure(fg_color=["#3a7ebf", "#1f538d"], border_width=0)
        self.btn_mode_format.configure(fg_color="transparent", border_width=2)
        self.ui_format.grid_forget(); self.ui_compress.grid(row=0, column=0, sticky="ew")
        # --- 下面是新增的两行，用于修复切换模式不刷新的问题 ---
        if self.entry_input.get() and not self.is_task_running:
            self.analyze_selected_path(self.entry_input.get())

    def show_format_mode(self):
        self.current_mode = "2"
        self.btn_mode_format.configure(fg_color=["#3a7ebf", "#1f538d"], border_width=0)
        self.btn_mode_compress.configure(fg_color="transparent", border_width=2)
        self.ui_compress.grid_forget(); self.ui_format.grid(row=0, column=0, sticky="ew")
        # --- 下面是新增的两行，用于修复切换模式不刷新的问题 ---
        if self.entry_input.get() and not self.is_task_running:
            self.analyze_selected_path(self.entry_input.get())

    def analyze_selected_path(self, path):
        self.textbox_log.delete("0.0", "end") # 修复：每次重新选文件，立刻清空下方杂乱日志！
        print(f"\n[AI 智能侦测] 正在分析...")
        if os.path.isdir(path):
            self.is_batch_mode = True
            self.current_media_info = {}
            self.show_perf_row()   
            self.hide_audio_row()
            if self.switch_batch_all.get() == 0: self.populate_batch_list() # 刷新独立列表
            print(f"(侦测结果) 目录输入模式，并发算力控制面板已解锁。")
        else:
            self.is_batch_mode = False
            self.hide_perf_row()   
            self.current_media_info = self.orchestrator.check_media_info(path)
            v_codec, a_codec = self.current_media_info.get('v_codec', '未知'), self.current_media_info.get('a_codec', '未知')
            
            if (v_codec in ("未知", "无") and a_codec != "无") and self.current_mode == "1":
                print("【严正警告】纯音频文件无法使用视频场景压制！已为您自动切换至格式化工厂。")
                self.show_format_mode() 
                return 
                
            print(f"(侦测结果) 体积: {round(self.current_media_info.get('size_mb', 0), 1)}MB | 视轨: {v_codec} | 音轨: {a_codec}")
            if a_codec == "无": self.show_audio_row()
            else: self.hide_audio_row()
        
        self.update_quality_estimates()
        self.update_format_estimates()

    # ==================== 核心执行任务 ====================
    def stop_task(self):
        if self.is_task_running:
            print("\n【紧急制动】正在呼叫底层无情强杀所有的 FFmpeg 进程，请稍候...")
            self.btn_stop.configure(state="disabled", text="终止中...")
            self.orchestrator.cleanup_processes()
            self.lbl_status.configure(text="任务已强制中止")

    def start_task(self):
        input_path = self.entry_input.get()
        if not input_path or not os.path.exists(input_path):
            print("(错误) 请先选择输入的文件或目录！"); return

        global_settings = self.get_gui_settings()
        
        files = []
        if os.path.isdir(input_path):
            valid_exts = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".3gp", ".ts", ".m2ts", ".vob", ".rmvb", ".ogv", ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".wma", ".opus", ".ac3")
            files = [str(f) for f in Path(input_path).iterdir() if f.is_file() and f.suffix.lower() in valid_exts]
        else:
            files = [input_path]

        out_dir = self.entry_output.get() or str(Path(input_path).parent if os.path.isfile(input_path) else input_path)
        custom_name = self.entry_rename.get().strip() if not self.is_batch_mode else None
        
        workers, threads = 1, 0
        if self.is_batch_mode:
            try: workers, threads = int(self.entry_workers.get()), int(self.entry_threads.get())
            except: pass

        # 核心：组装独立任务队列
        # 核心：组装独立任务队列
        tasks = []
        for f in files:
            file_setting = global_settings.copy()
            if self.is_batch_mode and self.switch_batch_all.get() == 0 and str(f) in self.file_setting_widgets:
                w = self.file_setting_widgets[str(f)]
                if self.current_mode == "1":
                    c_str = w["codec"].get()
                    file_setting["target_vcodec"] = {"1": "h264", "2": "hevc", "3": "av1"}.get(c_str[0], "hevc") if "AV1" not in c_str else "av1"
                    file_setting["quality_level"] = w["quality"].get()[0]
                    file_setting["target_fmt"] = w["format"].get()
                else:
                    file_setting["target_fmt"] = w["format"].get()
                    v_str = w["codec"].get()
                    file_setting["target_vcodec"] = {"1": "h264", "2": "hevc", "3": "av1"}.get(v_str[0], "h264")
                    q_str = w["quality"].get()
                    file_setting["compress_level"] = None if "n" in q_str else q_str[0]
            tasks.append((f, file_setting))

        self.textbox_log.delete("0.0", "end")
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal", text="⏹ 终止所有任务")
        self.progressbar.set(0)
        self.lbl_status.configure(text="正在处理中...") # 修复：直接显示正在处理
        self.orchestrator._is_user_aborted = False
        self.is_task_running = True
        
        threading.Thread(target=self.engine_run, args=(tasks, out_dir, workers, threads, custom_name), daemon=True).start()
    def get_gui_settings(self):
        a_codec = self.current_media_info.get("a_codec", "未知")
        v_codec = self.current_media_info.get("v_codec", "未知")
        
        # 【核心修复】：判断外部音频挂载逻辑
        ext_val = self.entry_audio.get() if self.entry_audio.winfo_ismapped() and self.entry_audio.get() else None
        # 只有在视频真的无声，且用户没有填入外部音频时，才传递 "none"
        if a_codec == "无" and not ext_val:
            ext_val = "none"

        s = {
            "choice": self.current_mode, 
            "ext_audio": ext_val,
            "v_codec": v_codec,
            "a_codec": a_codec
        }
        
        if self.current_mode == "1":
            codec_str = self.combo_codec.get()
            s.update({
                "video_category": self.combo_category.get()[0],
                "target_vcodec": {"1": "h264", "2": "hevc", "3": "av1"}.get(codec_str[0], "hevc") if "AV1" not in codec_str else "av1",
                "quality_level": self.combo_quality.get()[0],
                "target_fmt": self.combo_format.get()
            })
            # 智能修正 1：WebM 必须采用 AV1 编码
            if s["target_fmt"] == "webm" and s["target_vcodec"] in ("h264", "hevc"):
                s["target_vcodec"] = "av1"
                
        else:
            s.update({
                "target_fmt": self.combo_fmt_target.get(),
                "compress_level": None if "n" in self.combo_fmt_quality.get() else self.combo_fmt_quality.get()[0],
                "target_vcodec": {"1": "h264", "2": "hevc", "3": "av1"}.get(self.combo_fmt_vcodec.get()[0], "h264") if "无需视频" not in self.combo_fmt_vcodec.get() else "h264"
            })
            # 智能修正 2：纯音频强转 MP4 直通会报错，在此强制纠正为转码适配
            if s["target_fmt"] == "mp4" and s["v_codec"] in ("无", "未知") and not s.get("compress_level"):
                print("【GUI 智能修正】纯音频转 MP4 不支持无损直通，已强制为您开启音频适配转码！")
                s["compress_level"] = "1"
                
        return s

    def engine_run(self, tasks, out_dir, workers, threads, custom_name):
        from concurrent.futures import ThreadPoolExecutor, wait
        
        with ThreadPoolExecutor(max_workers=workers) as executor:
            # 迭代 tasks 列表，而不是单一的 settings
            futures = [executor.submit(self.orchestrator._execute_worker, f, out_dir, s, is_batch=len(tasks)>1, ffmpeg_threads=threads) for f, s in tasks]
            wait(futures)
            
        if not self.is_batch_mode and custom_name and self.is_task_running and len(tasks)>0:
            base_name, settings = Path(tasks[0][0]).stem, tasks[0][1]
            target_fmt = settings.get("target_fmt", "mkv")
            suffix = "_compressed." if settings["choice"] == "1" else "_converted."
            expected_out, final_out = Path(out_dir) / (base_name + suffix + target_fmt), Path(out_dir) / (custom_name + "." + target_fmt)

            if expected_out.exists():
                try:
                    if final_out.exists(): final_out.unlink()
                    expected_out.rename(final_out)
                except: pass

        if self.is_task_running:
            print("\n[所有任务已完成] 感谢使用 Komorebi。")
            self.lbl_status.configure(text="系统空闲中")
        else:
            print("\n[任务已被用户强行中止]")
            
        self.progressbar.set(1.0)
        self.is_task_running = False
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled", text="⏹ 终止所有任务")

if __name__ == "__main__":
    app = KomorebiApp()
    app.mainloop()