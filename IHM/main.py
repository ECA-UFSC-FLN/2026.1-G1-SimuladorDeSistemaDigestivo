import customtkinter as ctk
import serial
import serial.tools.list_ports
import threading
import time
import collections
import sqlite3
import csv
from tkinter import filedialog, messagebox
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# Configure CustomTkinter aesthetics
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class Database:
    def __init__(self, db_name="calibrations.db"):
        self.db_name = db_name
        self.init_db()

    def init_db(self):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS calibrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    description TEXT NOT NULL,
                    gain REAL NOT NULL,
                    offset REAL NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()

    def add_calibration(self, description, gain, offset):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO calibrations (description, gain, offset)
                VALUES (?, ?, ?)
            ''', (description, gain, offset))
            conn.commit()

    def get_all_calibrations(self):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM calibrations ORDER BY timestamp DESC')
            return cursor.fetchall()

    def update_description(self, cal_id, new_description):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE calibrations SET description = ? WHERE id = ?', (new_description, cal_id))
            conn.commit()

    def delete_calibration(self, cal_id):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM calibrations WHERE id = ?', (cal_id,))
            conn.commit()

class SerialApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Controle de pH")
        self.geometry("1000x700")
        self.minsize(800, 600)

        # Database
        self.db = Database()

        # Serial Configuration
        self.serial_port = None
        self.is_connected = False
        self.read_thread = None
        self.latest_tag_values = {}
        self.pending_input_values = {}
        
        # PI Controller state values
        self.current_kp = "-"
        self.current_ki = "-"
        self.current_umax = "-"
        self.current_uprev = "-"
        
        self.port_var = ctk.StringVar(value="None")
        self.baud_var = ctk.StringVar(value="9600")
        
        # Tag Variables
        self.tag_disp1_var = ctk.StringVar(value="pH")
        self.tag_disp2_var = ctk.StringVar(value="Tensao")
        self.tag_inp1_var = ctk.StringVar(value="step")
        self.tag_inp2_var = ctk.StringVar(value="ml")
        
        # Recording State
        self.is_recording = False
        self.recording_data = []

        # Layout Configuration (will be finalized when showing main interface)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # --- Sidebar (Settings & Tag Config) ---
        self.sidebar_frame = ctk.CTkFrame(self, width=250, corner_radius=0)
        self.sidebar_frame.grid_rowconfigure(18, weight=1)

        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="CONTROLE DE pH", font=ctk.CTkFont(size=20, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))

        # Settings Button
        self.settings_btn = ctk.CTkButton(self.sidebar_frame, text="Configurações", command=self.open_settings_modal, fg_color="#34495e", hover_color="#2c3e50")
        self.settings_btn.grid(row=1, column=0, padx=20, pady=(10, 5), sticky="ew")

        # Calibration Button
        self.calib_btn = ctk.CTkButton(self.sidebar_frame, text="Calibrar pH", command=self.open_calibration_modal, fg_color="#8e44ad", hover_color="#9b59b6", font=ctk.CTkFont(weight="bold"))
        self.calib_btn.grid(row=2, column=0, padx=20, pady=5, sticky="ew")

        # Connect/Disconnect Button
        self.connect_btn = ctk.CTkButton(self.sidebar_frame, text="Conectar", command=self.toggle_connection, fg_color="#2ecc71", hover_color="#27ae60", font=ctk.CTkFont(weight="bold"))
        self.connect_btn.grid(row=3, column=0, padx=20, pady=5, sticky="ew")

        # CSV Export Button
        self.record_btn = ctk.CTkButton(self.sidebar_frame, text="Iniciar Gravação CSV", command=self.toggle_recording, fg_color="#e67e22", hover_color="#d35400", font=ctk.CTkFont(weight="bold"))
        self.record_btn.grid(row=4, column=0, padx=20, pady=(5, 20), sticky="ew")

        # Modo Toggle (Controlador Automático)
        self.pi_mode_switch = ctk.CTkSwitch(self.sidebar_frame, text="Contr. Automático", command=self.toggle_pi_mode, font=ctk.CTkFont(weight="bold"))
        self.pi_mode_switch.grid(row=5, column=0, padx=20, pady=10, sticky="ew")

        # --- Sidebar Row Weight ---
        self.sidebar_frame.grid_rowconfigure(6, weight=1)

        # --- Main View ---
        self.main_container = ctk.CTkFrame(self, fg_color="transparent")
        self.main_container.grid_rowconfigure(0, weight=1)
        self.main_container.grid_columnconfigure(0, weight=1)

        # Data history setup
        self.current_val1 = 0.0
        self.current_val2 = 0.0
        self.history_len = 100
        self.time_history = list(range(self.history_len))
        self.tag1_history = collections.deque([0.0]*self.history_len, maxlen=self.history_len)
        self.tag2_history = collections.deque([0.0]*self.history_len, maxlen=self.history_len)

        # 1. Dashboard (Top)
        self.dashboard_frame = ctk.CTkFrame(self.main_container)
        self.dashboard_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        self.dashboard_frame.grid_columnconfigure((0, 1), weight=1)
        self.dashboard_frame.grid_rowconfigure(0, minsize=220, weight=0)
        self.dashboard_frame.grid_rowconfigure(1, weight=1) # Chart row gets remaining space

        # Numerical Displays (Visores)
        self.create_display_box(self.dashboard_frame, 0, 0, self.tag_disp1_var.get(), "0.00", "visor1_val", "visor1_title", columnspan=1)

        # Containers para os modos de controle (Manual / Automático)
        self.manual_controls_frame = ctk.CTkFrame(self.dashboard_frame, fg_color="transparent")
        self.manual_controls_frame.grid_columnconfigure(0, weight=1)
        self.manual_controls_frame.grid_rowconfigure((0, 1), minsize=105, weight=1)

        self.auto_controls_frame = ctk.CTkFrame(self.dashboard_frame, fg_color="transparent")
        self.auto_controls_frame.grid_columnconfigure(0, weight=1)
        self.auto_controls_frame.grid_rowconfigure((0, 1), minsize=105, weight=1)

        # Numerical Inputs (Modo Manual)
        self.create_input_box(self.manual_controls_frame, 0, 0, self.tag_inp1_var.get(), "input1_val", "input1_title", self.send_input1)
        self.create_input_box(self.manual_controls_frame, 1, 0, self.tag_inp2_var.get(), "input2_val", "input2_title", self.send_input2)

        # Controls for Modo Automático
        self.create_input_box(self.auto_controls_frame, 0, 0, "pH alvo", "pi_sp_entry", "pi_sp_title", self.send_pi_sp)

        # Botão para abrir o modal de parâmetros no modo automático
        self.pi_param_frame = ctk.CTkFrame(self.auto_controls_frame, border_width=1, border_color="#34495e")
        self.pi_param_frame.grid(row=1, column=0, padx=10, pady=10, sticky="nsew")
        
        self.pi_status_label = ctk.CTkLabel(self.pi_param_frame, text="Status: Desconectado", font=ctk.CTkFont(size=12, weight="bold"), text_color="gray")
        self.pi_status_label.pack(pady=(12, 4))
        
        self.pi_param_btn = ctk.CTkButton(self.pi_param_frame, text="Parâmetros do Controlador", command=self.open_pi_parameters_modal, fg_color="#3498db", hover_color="#2980b9", font=ctk.CTkFont(weight="bold"))
        self.pi_param_btn.pack(pady=(4, 12), padx=20, fill="x")

        # Chart Frame
        self.chart_frame = ctk.CTkFrame(self.dashboard_frame, fg_color="#2b2b2b", corner_radius=0)
        self.chart_frame.grid(row=1, column=0, columnspan=2, padx=10, pady=10, sticky="nsew")
        
        plt.rc('font', size=4)
        self.fig, self.ax = plt.subplots(figsize=(6, 3), dpi=100)
        self.fig.patch.set_facecolor('#2b2b2b')
        self.ax.set_facecolor('#2b2b2b')
        self.ax.tick_params(axis='x', colors='white', labelsize=4)
        self.ax.tick_params(axis='y', colors='white', labelsize=4)
        self.ax.spines['bottom'].set_color('white')
        self.ax.spines['top'].set_color('white')
        self.ax.spines['left'].set_color('white')
        self.ax.spines['right'].set_color('white')
        
        self.line1, = self.ax.plot(self.time_history, self.tag1_history, label=self.tag_disp1_var.get().upper(), color='#3498db')
        self.ax.legend(loc='upper right', facecolor='#2b2b2b', labelcolor='white', fontsize=4)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.chart_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        
        # Start chart update loop
        self.after(100, self.update_chart_loop)

        # Default operation mode
        self.set_operation_mode(False)

        # 2. Console/Log (Bottom) - HIDDEN BY DEFAULT
        self.log_frame = ctk.CTkFrame(self.main_container)
        self.is_console_visible = False
        self.log_frame.grid_rowconfigure(1, weight=1)
        self.log_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self.log_frame, text="CONSOLE SERIAL (DADOS BRUTOS)", font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=0, padx=10, pady=5, sticky="w")
        self.log_textbox = ctk.CTkTextbox(self.log_frame, state="disabled", font=("Courier", 12))
        self.log_textbox.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")

        # Quick Send Bar
        self.quick_send_frame = ctk.CTkFrame(self.log_frame, height=40)
        self.quick_send_frame.grid(row=2, column=0, padx=10, pady=(0, 10), sticky="ew")
        self.quick_entry = ctk.CTkEntry(self.quick_send_frame, placeholder_text="Comando rápido...")
        self.quick_entry.pack(side="left", fill="x", expand=True, padx=(5, 5), pady=5)
        self.quick_entry.bind("<Return>", lambda e: self.send_raw())
        ctk.CTkButton(self.quick_send_frame, text="Enviar", width=60, command=self.send_raw).pack(side="right", padx=5, pady=5)

        self.create_intro_screen()

    def create_intro_screen(self):
        # Configure grid for single centered column
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)
        self.grid_rowconfigure(0, weight=1)
        
        self.intro_frame = ctk.CTkFrame(self, fg_color="#1e1e1e")
        self.intro_frame.grid(row=0, column=0, sticky="nsew")
        
        # Center card container
        card = ctk.CTkFrame(self.intro_frame, width=500, height=380, fg_color="#2b2b2b", border_width=1, border_color="#34495e", corner_radius=15)
        card.place(relx=0.5, rely=0.5, anchor="center")
        
        # Title
        title_lbl = ctk.CTkLabel(card, text="Controle de pH", font=ctk.CTkFont(size=24, weight="bold"), text_color="#3498db")
        title_lbl.pack(pady=(35, 10))
        
        subtitle_lbl = ctk.CTkLabel(card, text="Simulador de Sistema Digestivo", font=ctk.CTkFont(size=14, weight="bold"), text_color="gray")
        subtitle_lbl.pack(pady=(0, 30))
        
        # Instruction text
        inst_text = "Para começar, conecte o cabo USB do controlador\n" \
                    "ao computador e certifique-se de que o dispositivo está ligado.\n\n" \
                    "O sistema tentará realizar a conexão automática."
        inst_lbl = ctk.CTkLabel(card, text=inst_text, font=ctk.CTkFont(size=13), justify="center", text_color="white")
        inst_lbl.pack(pady=(0, 30), padx=30)
        
        # Button: Start Auto Connection
        self.start_conn_btn = ctk.CTkButton(card, text="Iniciar Conexão", height=45, command=self.start_auto_connection, fg_color="#2ecc71", hover_color="#27ae60", font=ctk.CTkFont(size=14, weight="bold"))
        self.start_conn_btn.pack(fill="x", padx=60, pady=10)
        
        # Button: Manual setup / Bypass
        skip_btn = ctk.CTkButton(card, text="Pular / Configuração Manual", height=35, command=self.bypass_to_main, fg_color="transparent", hover_color="#34495e", border_width=1, border_color="#34495e", font=ctk.CTkFont(size=12))
        skip_btn.pack(fill="x", padx=60, pady=(5, 20))

    def bypass_to_main(self):
        self.show_main_interface()

    def start_auto_connection(self):
        self.start_conn_btn.configure(state="disabled", text="Conectando...")
        self.update_idletasks()
        
        has_arduino = self.refresh_ports()
        
        if not has_arduino:
            self.start_conn_btn.configure(state="normal", text="Iniciar Conexão")
            self.show_connection_error()
            return
        
        success = self.connect_serial()
        
        if success:
            self.show_main_interface()
        else:
            self.start_conn_btn.configure(state="normal", text="Iniciar Conexão")
            self.show_connection_error()

    def show_connection_error(self):
        messagebox.showwarning(
            "Falha na Conexão",
            "Não foi possível conectar ao simulador automaticamente.\n\n"
            "Por favor, verifique a conexão física (cabo USB) e certifique-se de que "
            "o simulador está ligado.\n\n"
            "A interface principal será exibida para que você configure a conexão "
            "manualmente através do botão 'Configurações'."
        )
        self.show_main_interface()

    def show_main_interface(self):
        if hasattr(self, 'intro_frame') and self.intro_frame:
            self.intro_frame.grid_forget()
            self.intro_frame.destroy()
            self.intro_frame = None
            
        # Configure columns for main dashboard layout
        self.grid_columnconfigure(0, weight=0) # Sidebar
        self.grid_columnconfigure(1, weight=1) # Main View
        self.grid_rowconfigure(0, weight=1)
        
        # Grid sidebar and main container
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.main_container.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")

    def toggle_pi_mode(self):
        if self.pi_mode_switch.get() == 1:
            self._write_serial("pi:on\n")
        else:
            self._write_serial("pi:off\n")
        self.after(200, self.request_pi_info)

    def send_pi_sp(self):
        entry = getattr(self, "pi_sp_entry", None)
        if entry:
            val = entry.get().strip()
            if val:
                self._write_serial(f"sp:{val}\n")
                entry.delete(0, 'end')
                self.after(200, self.request_pi_info)

    def send_pi_kp(self):
        entry = getattr(self, "pi_kp_entry", None)
        if entry:
            val = entry.get().strip()
            if val:
                self._write_serial(f"kp:{val}\n")
                entry.delete(0, 'end')
                self.after(200, self.request_pi_info)

    def send_pi_ki(self):
        entry = getattr(self, "pi_ki_entry", None)
        if entry:
            val = entry.get().strip()
            if val:
                self._write_serial(f"ki:{val}\n")
                entry.delete(0, 'end')
                self.after(200, self.request_pi_info)

    def send_pi_v0(self):
        entry = getattr(self, "pi_v0_entry", None)
        if entry:
            val = entry.get().strip()
            if val:
                self._write_serial(f"v0:{val}\n")
                entry.delete(0, 'end')
                self.after(200, self.request_pi_info)

    def send_pi_int(self):
        entry = getattr(self, "pi_int_entry", None)
        if entry:
            val = entry.get().strip()
            if val:
                self._write_serial(f"piint:{val}\n")
                entry.delete(0, 'end')
                self.after(200, self.request_pi_info)

    def send_pi_reset(self):
        self._write_serial("zpi\n")
        self.after(200, self.request_pi_info)

    def request_pi_info(self):
        self._write_serial("piinfo\n")

    def update_kp_ki_display(self):
        self.update_modal_labels()

    def update_u_display(self):
        self.update_modal_labels()

    def set_operation_mode(self, is_auto):
        if is_auto:
            self.pi_mode_switch.select()
            self.pi_status_label.configure(text="Status: ATIVO", text_color="#2ecc71")
            
            # Switch view frames
            self.manual_controls_frame.grid_forget()
            self.auto_controls_frame.grid(row=0, column=1, sticky="nsew")
        else:
            self.pi_mode_switch.deselect()
            self.pi_status_label.configure(text="Status: INATIVO", text_color="#e74c3c")
            
            # Switch view frames
            self.auto_controls_frame.grid_forget()
            self.manual_controls_frame.grid(row=0, column=1, sticky="nsew")

    def open_pi_parameters_modal(self):
        PiParametersModal(self)

    def update_modal_labels(self):
        if hasattr(self, "modal_info_labels") and self.modal_info_labels:
            try:
                sp = self.latest_tag_values.get("Setpoint (SP)", "-")
                if isinstance(sp, float): sp = f"{sp:.2f}"
                
                v0 = self.latest_tag_values.get("Volume Reator V0", "-")
                kp = getattr(self, "current_kp", "-")
                ki = getattr(self, "current_ki", "-")
                
                err = self.latest_tag_values.get("Erro acumulado (I)", "-")
                if isinstance(err, float): err = f"{err:.4f}"
                
                umax = getattr(self, "current_umax", "-")
                uprev = getattr(self, "current_uprev", "-")
                
                self.modal_info_labels["sp_val"].configure(text=str(sp))
                self.modal_info_labels["v0_val"].configure(text=str(v0))
                self.modal_info_labels["k_val"].configure(text=f"{kp} / {ki}")
                self.modal_info_labels["err_val"].configure(text=str(err))
                self.modal_info_labels["u_val"].configure(text=f"{umax} / {uprev}")
            except Exception:
                pass



    def create_display_box(self, parent, row, col, title, initial_val, val_attr, title_attr, columnspan=1):
        frame = ctk.CTkFrame(parent, border_width=1, border_color="#34495e")
        frame.grid(row=row, column=col, columnspan=columnspan, padx=10, pady=10, sticky="nsew")
        title_label = ctk.CTkLabel(frame, text=title.upper(), font=ctk.CTkFont(size=14, weight="bold"))
        title_label.pack(pady=(25, 10))
        setattr(self, title_attr, title_label)
        
        val_label = ctk.CTkLabel(frame, text=initial_val, font=ctk.CTkFont(size=72, weight="bold"), text_color="#3498db")
        val_label.pack(pady=(10, 35))
        setattr(self, val_attr, val_label)

    def create_input_box(self, parent, row, col, title, entry_attr, title_attr, command):
        frame = ctk.CTkFrame(parent, border_width=1, border_color="#34495e")
        frame.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")
        title_label = ctk.CTkLabel(frame, text=title.upper(), font=ctk.CTkFont(size=12, weight="bold"))
        title_label.pack(pady=(10, 0))
        setattr(self, title_attr, title_label)
        
        inner_frame = ctk.CTkFrame(frame, fg_color="transparent")
        inner_frame.pack(padx=10, pady=10, fill="x")
        
        entry = ctk.CTkEntry(inner_frame, placeholder_text="Valor...")
        entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        setattr(self, entry_attr, entry)
        
        btn = ctk.CTkButton(inner_frame, text="SET", width=60, command=command)
        btn.pack(side="right")

    def toggle_console(self):
        if self.is_console_visible:
            self.log_frame.grid_forget()
            self.main_container.grid_rowconfigure(1, weight=0)
            self.is_console_visible = False
        else:
            self.main_container.grid_rowconfigure(1, weight=1)
            self.log_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
            self.is_console_visible = True
        
        # If SettingsModal is open, update its console button text
        if hasattr(self, 'settings_modal') and self.settings_modal.winfo_exists() and hasattr(self.settings_modal, 'console_btn'):
            btn_text = "Esconder Console" if self.is_console_visible else "Exibir Console"
            self.settings_modal.console_btn.configure(text=btn_text)

    def log_message(self, message, source="System"):
        self.log_textbox.configure(state="normal")
        ts = time.strftime("%H:%M:%S")
        self.log_textbox.insert("end", f"[{ts}] {source}: {message}\n")
        self.log_textbox.see("end")
        self.log_textbox.configure(state="disabled")

    def refresh_ports(self):
        ports = serial.tools.list_ports.comports()
        port_list = []
        arduino_port = None
        
        for port in ports:
            port_list.append(port.device)
            # Metadata analysis to identify Arduinos or common serial chips
            desc = port.description.lower()
            hwid = port.hwid.lower()
            if any(key in desc or key in hwid for key in ["arduino", "ch340", "genuino", "usb serial", "usb-serial", "usbmodem", "usbserial", "cp210", "pl2303", "ft232", "wchusb"]):
                arduino_port = port.device
        
        # If settings modal is open, we need to update its option menu values
        if hasattr(self, 'settings_modal') and self.settings_modal.winfo_exists():
            self.settings_modal.port_optionmenu.configure(values=port_list if port_list else ["Sem portas"])

        if not port_list:
            port_list = ["Sem portas"]
            self.port_var.set("Sem portas")
            return False
        else:
            if arduino_port:
                self.port_var.set(arduino_port)
                self.log_message(f"Auto-detect: Arduino encontrado em {arduino_port}", "Sistema")
                return True
            else:
                if self.port_var.get() not in port_list:
                    self.port_var.set(port_list[0])
                return False

    def toggle_connection(self):
        if self.is_connected: self.disconnect_serial()
        else: self.connect_serial()

    def connect_serial(self):
        port = self.port_var.get()
        baud_str = self.baud_var.get()
        if port == "Sem portas" or not port:
            self.log_message("Conexão falhou: Nenhuma porta selecionada.", "Sistema")
            return False
        try:
            baud = int(baud_str)
            self.serial_port = serial.Serial(port, baud, timeout=0.1)
            self.is_connected = True
            self.connect_btn.configure(text="Desconectar", fg_color="#e74c3c", hover_color="#c0392b")
            self.log_message(f"Conectado a {port}")
            self.read_thread = threading.Thread(target=self.read_loop, daemon=True)
            self.read_thread.start()
            
            # Initial status update
            self.pi_status_label.configure(text="Status: Lendo...", text_color="orange")
            # Setup flag to wait for main loop telemetry before querying PI info
            self.setup_complete = False
            return True
        except Exception as e:
            self.log_message(f"Erro: {e}")
            return False

    def disconnect_serial(self):
        self.is_connected = False
        if self.serial_port: self.serial_port.close()
        self.connect_btn.configure(text="Conectar", fg_color="#2ecc71", hover_color="#27ae60")
        self.log_message("Desconectado")
        self.pi_status_label.configure(text="Status: Desconectado", text_color="gray")
        self.pi_mode_switch.deselect()
        self.pi_mode_switch.configure(state="disabled")
        self.set_operation_mode(False)

    def read_loop(self):
        while self.is_connected:
            try:
                if self.serial_port.in_waiting > 0:
                    line = self.serial_port.readline().decode('utf-8', errors='ignore').strip()
                    if line: self.after(0, self.process_incoming, line)
                time.sleep(0.01)
            except: break

    def process_incoming(self, line):
        self.log_message(line, "RX")
        
        # Se a linha termina com "?", exibe um modal para resposta
        if line.endswith("?"):
            self.after(0, self.prompt_question, line)
            return

        # Check for PI status confirmations
        if ">>> Controlador PI ativado" in line:
            self.set_operation_mode(True)
        elif ">>> Controlador PI desativado" in line:
            self.set_operation_mode(False)

        # Parse real-time PI controller telemetry
        if line.startswith("[PI]"):
            try:
                parts = line[4:].strip().split("|")
                for part in parts:
                    if ":" in part:
                        p_tag, p_val = part.split(":", 1)
                        p_tag = p_tag.strip()
                        p_val = p_val.strip()
                        if p_val.endswith(" ml"):
                            p_val = p_val[:-3].strip()
                        
                        try:
                            val_float = float(p_val)
                            if p_tag == "SP":
                                self.latest_tag_values["Setpoint"] = val_float
                            elif p_tag == "Erro":
                                self.latest_tag_values["Erro"] = val_float
                            elif p_tag == "u_out":
                                self.latest_tag_values["Volume Total Inserido"] = val_float
                        except ValueError:
                            pass
            except Exception:
                pass

        if ":" in line:
            try:
                tag, val = line.split(":", 1)
                tag = tag.strip()
                val = val.strip()
                
                # Check for PI info tag-value pairs
                if tag == "Ativo":
                    is_active = (val == "SIM")
                    self.set_operation_mode(is_active)
                elif tag == "Setpoint (SP)":
                    try:
                        self.latest_tag_values["Setpoint"] = float(val)
                    except ValueError:
                        pass
                elif tag == "Volume Reator V0":
                    pass
                elif tag == "Kp":
                    self.current_kp = val
                    self.update_kp_ki_display()
                elif tag == "Ki":
                    self.current_ki = val
                    self.update_kp_ki_display()
                elif tag == "Erro acumulado (I)":
                    try:
                        self.latest_tag_values["Erro"] = float(val)
                    except ValueError:
                        pass
                elif tag == "u_max":
                    self.current_umax = val
                    self.update_u_display()
                elif tag == "u_prev":
                    self.current_uprev = val
                    self.update_u_display()
                    try:
                        self.latest_tag_values["Volume Total Inserido"] = float(val)
                    except ValueError:
                        pass

                try:
                    self.latest_tag_values[tag] = float(val)
                except ValueError:
                    pass

                self.update_modal_labels()

                if tag == self.tag_disp1_var.get().strip():
                    self.visor1_val.configure(text=val)
                    try: self.current_val1 = float(val)
                    except ValueError: pass
                elif tag == self.tag_disp2_var.get().strip():
                    try: self.current_val2 = float(val)
                    except ValueError: pass

                # If telemetry data is received, the setup questions are complete and loop has started.
                if tag in [self.tag_disp1_var.get().strip(), self.tag_disp2_var.get().strip()]:
                    if not getattr(self, "setup_complete", False):
                        self.setup_complete = True
                        self.after(500, self.request_pi_info)
            except: pass

    def prompt_question(self, question):
        dialog = ctk.CTkInputDialog(text=question, title="Pergunta do Arduino")
        value = dialog.get_input()
        if value is not None:
            response = value.strip()
            self._write_serial(response + "\n")
            self.log_message(f"Resposta enviada: {response}", "Sistema")
        else:
            self.log_message("Entrada cancelada pelo usuário.", "Sistema")

    def update_chart_loop(self):
        if self.is_connected:
            self.tag1_history.append(self.current_val1)
            self.tag2_history.append(self.current_val2)
            self.line1.set_ydata(self.tag1_history)
            self.ax.relim()
            self.ax.autoscale_view()
            self.canvas.draw_idle()
        self.after(100, self.update_chart_loop)

    def send_input1(self):
        tag = self.tag_inp1_var.get().strip()
        val = self.input1_val.get().strip()
        self._send_formatted(tag, val)

    def send_input2(self):
        tag = self.tag_inp2_var.get().strip()
        val = self.input2_val.get().strip()
        self._send_formatted(tag, val)

    def _send_formatted(self, tag, val):
        if not val: return
        self._write_serial(f"{tag}:{val}\n")
        # Store locally for UI tracking
        try:
            val_float = float(val)
            self.latest_tag_values[tag] = val_float
            self.pending_input_values[tag] = val_float
        except ValueError:
            self.latest_tag_values[tag] = val
            self.pending_input_values[tag] = val

    def toggle_recording(self):
        if not self.is_recording:
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self):
        self.is_recording = True
        self.recording_data = []
        self.record_btn.configure(text="Parar e Salvar CSV", fg_color="#c0392b", hover_color="#a93226")
        self.log_message("Gravação de dados iniciada.", "Sistema")
        self.record_snapshot_loop()

    def record_snapshot_loop(self):
        if self.is_recording:
            # Capture current state of active tags
            tags = [
                self.tag_disp1_var.get().strip(),
                self.tag_disp2_var.get().strip(),
                self.tag_inp1_var.get().strip(),
                self.tag_inp2_var.get().strip()
            ]
            
            snapshot = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S") + f".{int(time.time()*1000)%1000:03d}",
                "millis": int(time.time() * 1000)
            }
            
            # Add each tag value to the snapshot
            input_tags = [self.tag_inp1_var.get().strip(), self.tag_inp2_var.get().strip()]
            
            for tag in tags:
                if tag:
                    if tag in input_tags:
                        # For input tags, use pending value if exists, else 0
                        snapshot[tag] = self.pending_input_values.get(tag, 0)
                        # Clear the pending value after it's recorded
                        self.pending_input_values[tag] = 0
                    else:
                        # For display tags, use the latest received value
                        snapshot[tag] = self.latest_tag_values.get(tag, "")
            
            # Add control variables to the snapshot
            snapshot["Setpoint"] = self.latest_tag_values.get("Setpoint", "")
            snapshot["Erro"] = self.latest_tag_values.get("Erro", "")
            snapshot["Volume Total Inserido"] = self.latest_tag_values.get("Volume Total Inserido", "")
            
            self.recording_data.append(snapshot)
            # Sample every 100ms
            self.after(100, self.record_snapshot_loop)

    def stop_recording(self):
        self.is_recording = False
        self.record_btn.configure(text="Iniciar Gravação CSV", fg_color="#e67e22", hover_color="#d35400")
        
        if not self.recording_data:
            self.log_message("Nenhum dado capturado para salvar.", "Sistema")
            return

        file_path = filedialog.asksaveasfilename(
            title="Salvar Dados CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        
        if file_path:
            try:
                with open(file_path, mode='w', newline='') as f:
                    all_keys = set()
                    for rec in self.recording_data:
                        all_keys.update(rec.keys())
                    
                    ordered_keys = [
                        "timestamp", 
                        "millis", 
                        self.tag_disp1_var.get().strip(), 
                        self.tag_disp2_var.get().strip(), 
                        self.tag_inp1_var.get().strip(), 
                        self.tag_inp2_var.get().strip(),
                        "Setpoint",
                        "Erro",
                        "Volume Total Inserido"
                    ]
                    # Filter out empty or duplicate names
                    ordered_keys = [k for k in ordered_keys if k]
                    
                    # Deduplicate ordered_keys while keeping order
                    seen = set()
                    final_ordered_keys = []
                    for k in ordered_keys:
                        if k not in seen:
                            seen.add(k)
                            final_ordered_keys.append(k)
                            
                    other_keys = sorted(list(all_keys - set(final_ordered_keys)))
                    fieldnames = final_ordered_keys + other_keys
                    
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(self.recording_data)
                
                self.log_message(f"Dados salvos com sucesso: {file_path}", "Sistema")
            except Exception as e:
                self.log_message(f"Erro ao salvar CSV: {e}", "Sistema")

    def send_raw(self):
        msg = self.quick_entry.get().strip()
        if msg:
            self._write_serial(msg + "\n")
            self.quick_entry.delete(0, 'end')

    def _write_serial(self, msg):
        if self.is_connected and self.serial_port:
            try:
                self.serial_port.write(msg.encode('utf-8'))
                self.log_message(msg.strip(), "TX")
            except Exception as e:
                self.log_message(f"Erro TX: {e}")

    def open_calibration_modal(self):
        if not self.is_connected:
            self.log_message("Erro: Conecte-se ao Arduino antes de calibrar.", "Sistema")
            return
        CalibrationModal(self)

    def open_settings_modal(self):
        self.settings_modal = SettingsModal(self)

class CalibrationModal(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Calibração do Sensor de pH")
        self.geometry("450x380")
        self.resizable(False, False)
        
        self.transient(app)
        self.grab_set()
        
        self.step = 0
        self.voltage_tag = ctk.StringVar(value="Tensao")
        self.samples = []
        self.v_buffer1 = 0.0
        self.v_buffer2 = 0.0
        
        self.container = ctk.CTkFrame(self)
        self.container.pack(fill="both", expand=True, padx=20, pady=20)
        
        self.draw_mode_selection()
        
    def clear_container(self):
        for widget in self.container.winfo_children():
            widget.destroy()

    def draw_mode_selection(self):
        self.clear_container()
        self.step = -1
        ctk.CTkLabel(self.container, text="Calibração do Sensor de pH", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(10, 30))
        
        ctk.CTkButton(self.container, text="Nova Calibração", height=50, command=self.draw_step_0, fg_color="#3498db", hover_color="#2980b9").pack(fill="x", pady=10)
        ctk.CTkButton(self.container, text="Carregar Calibração Salva", height=50, command=self.draw_load_screen, fg_color="#9b59b6", hover_color="#8e44ad").pack(fill="x", pady=10)

    def draw_load_screen(self):
        self.clear_container()
        self.step = -2
        ctk.CTkLabel(self.container, text="Calibrações Salvas", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(10, 10))
        
        scroll_frame = ctk.CTkScrollableFrame(self.container, height=200)
        scroll_frame.pack(fill="both", expand=True, pady=10)
        
        calibrations = self.app.db.get_all_calibrations()
        if not calibrations:
            ctk.CTkLabel(scroll_frame, text="Nenhuma calibração encontrada.").pack(pady=20)
        
        for cal in calibrations:
            id, desc, gain, offset, ts = cal
            item_frame = ctk.CTkFrame(scroll_frame)
            item_frame.pack(fill="x", pady=5, padx=5)
            
            info_text = f"{desc}\nGain: {gain:.4f} | Offset: {offset:.4f}"
            ctk.CTkLabel(item_frame, text=info_text, justify="left", font=ctk.CTkFont(size=11)).pack(side="left", padx=10, pady=5)
            
            # Action Buttons Frame
            actions_frame = ctk.CTkFrame(item_frame, fg_color="transparent")
            actions_frame.pack(side="right", padx=5)
            
            ctk.CTkButton(actions_frame, text="Usar", width=50, height=24, command=lambda c=cal: self.use_saved_calibration(c)).pack(side="left", padx=2)
            ctk.CTkButton(actions_frame, text="Edit", width=50, height=24, fg_color="#f39c12", hover_color="#e67e22", command=lambda cid=id, d=desc: self.edit_description(cid, d)).pack(side="left", padx=2)
            ctk.CTkButton(actions_frame, text="X", width=30, height=24, fg_color="#e74c3c", hover_color="#c0392b", command=lambda cid=id: self.delete_calibration(cid)).pack(side="left", padx=2)

        ctk.CTkButton(self.container, text="Voltar", command=self.draw_mode_selection).pack(pady=10)

    def use_saved_calibration(self, cal):
        id, desc, gain, offset, ts = cal
        self.final_gain = gain
        self.final_offset = offset
        self.finish_calibration(save=False)

    def edit_description(self, cal_id, old_desc):
        dialog = ctk.CTkInputDialog(text="Nova descrição:", title="Editar Calibração")
        new_desc = dialog.get_input()
        if new_desc:
            self.app.db.update_description(cal_id, new_desc)
            self.draw_load_screen()

    def delete_calibration(self, cal_id):
        self.app.db.delete_calibration(cal_id)
        self.draw_load_screen()

    def draw_step_0(self):
        self.clear_container()
        self.step = 0
        ctk.CTkLabel(self.container, text="Passo 1: Configuração", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(10, 20))
        ctk.CTkLabel(self.container, text="Informe a Tag Serial que envia a tensão lida:").pack(anchor="w")
        
        entry = ctk.CTkEntry(self.container, textvariable=self.voltage_tag)
        entry.pack(fill="x", pady=(5, 20))
        
        btn = ctk.CTkButton(self.container, text="Próximo", command=self.draw_step_1)
        btn.pack(pady=10)
        ctk.CTkButton(self.container, text="Voltar", command=self.draw_mode_selection, fg_color="transparent", border_width=1).pack(pady=5)

    def draw_step_1(self):
        self.clear_container()
        self.step = 1
        ctk.CTkLabel(self.container, text="Passo 2: Buffer pH 7", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(10, 10))
        ctk.CTkLabel(self.container, text="Lave o sensor com água destilada, seque e insira-o na solução de pH 7.", wraplength=400).pack(pady=5)
        
        self.val_label = ctk.CTkLabel(self.container, text="Tensão atual: -- V", font=ctk.CTkFont(size=14, weight="bold"), text_color="#3498db")
        self.val_label.pack(pady=15)
        
        self.btn_action = ctk.CTkButton(self.container, text="Coletar Amostras", command=lambda: self.start_collection(7))
        self.btn_action.pack(pady=10)
        
        self.update_live_voltage()
        
    def draw_step_2(self):
        self.clear_container()
        self.step = 2
        ctk.CTkLabel(self.container, text="Passo 3: Buffer pH 4", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(10, 10))
        ctk.CTkLabel(self.container, text="Lave o sensor, seque e insira-o na solução de pH 4.", wraplength=400).pack(pady=5)
        
        self.val_label = ctk.CTkLabel(self.container, text="Tensão atual: -- V", font=ctk.CTkFont(size=14, weight="bold"), text_color="#3498db")
        self.val_label.pack(pady=15)
        
        self.btn_action = ctk.CTkButton(self.container, text="Coletar Amostras", command=lambda: self.start_collection(4))
        self.btn_action.pack(pady=10)
        
        self.update_live_voltage()

    def draw_step_3(self):
        self.clear_container()
        self.step = 3
        
        v_diff = (self.v_buffer1 - self.v_buffer2)
        if v_diff == 0:
            gain = 0
            offset = 0
        else:
            gain = (7.0 - 4.0) / v_diff
            offset = 7.0 - (gain * self.v_buffer1)
            
        self.final_gain = gain
        self.final_offset = offset
        
        ctk.CTkLabel(self.container, text="Passo 4: Resultados", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(10, 10))
        
        res_frame = ctk.CTkFrame(self.container)
        res_frame.pack(fill="x", pady=10)
        
        ctk.CTkLabel(res_frame, text=f"Tensão Buffer 7: {self.v_buffer1:.4f} V").pack(pady=2)
        ctk.CTkLabel(res_frame, text=f"Tensão Buffer 4: {self.v_buffer2:.4f} V").pack(pady=2)
        ctk.CTkLabel(res_frame, text=f"Gain Calculado: {gain:.4f}", text_color="#f1c40f", font=ctk.CTkFont(weight="bold")).pack(pady=5)
        ctk.CTkLabel(res_frame, text=f"Offset Calculado: {offset:.4f}", text_color="#f1c40f", font=ctk.CTkFont(weight="bold")).pack(pady=5)
        
        btn = ctk.CTkButton(self.container, text="Enviar e Finalizar", command=lambda: self.finish_calibration(save=True), fg_color="#2ecc71", hover_color="#27ae60")
        btn.pack(pady=15)
        ctk.CTkButton(self.container, text="Descartar", command=self.destroy, fg_color="transparent", border_width=1).pack()

    def update_live_voltage(self):
        if self.step in [1, 2] and self.winfo_exists():
            tag = self.voltage_tag.get().strip()
            val = self.app.latest_tag_values.get(tag)
            if val is not None:
                self.val_label.configure(text=f"Tensão atual: {val:.4f} V")
            self.after(200, self.update_live_voltage)
            
    def start_collection(self, ph_val):
        self.btn_action.configure(state="disabled", text="Coletando...")
        self.samples = []
        self.collect_sample(ph_val, 50)
        
    def collect_sample(self, ph_val, remaining):
        if remaining > 0:
            tag = self.voltage_tag.get().strip()
            val = self.app.latest_tag_values.get(tag)
            if val is not None:
                self.samples.append(val)
                self.btn_action.configure(text=f"Coletando... ({len(self.samples)}/50)")
                self.after(100, lambda: self.collect_sample(ph_val, remaining - 1))
            else:
                self.after(100, lambda: self.collect_sample(ph_val, remaining))
        else:
            if len(self.samples) > 0:
                avg = sum(self.samples) / len(self.samples)
            else:
                avg = 0.0
                
            if ph_val == 7:
                self.v_buffer1 = avg
                self.draw_step_2()
            else:
                self.v_buffer2 = avg
                self.draw_step_3()

    def finish_calibration(self, save=False):
        if save:
            dialog = ctk.CTkInputDialog(text="Descrição para esta calibração:", title="Salvar Calibração")
            desc = dialog.get_input()
            if desc:
                self.app.db.add_calibration(desc, self.final_gain, self.final_offset)
            else:
                # Se cancelar o input, podemos ou não salvar com nome padrão
                # Vamos assumir que se cancelar ele não quer salvar no banco, mas quer aplicar
                pass

        self.app._write_serial(f"gain:{self.final_gain:.4f}\n")
        self.app.after(100, lambda: self.app._write_serial(f"offset:{self.final_offset:.4f}\n"))
        self.app.log_message(f"Calibração aplicada: Gain={self.final_gain:.4f}, Offset={self.final_offset:.4f}", "Sistema")
        self.destroy()

class SettingsModal(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Configurações do Sistema")
        self.geometry("400x350")
        self.resizable(False, False)
        
        self.transient(app)
        self.grab_set()
        
        self.container = ctk.CTkFrame(self)
        self.container.pack(fill="both", expand=True, padx=20, pady=20)
        
        ctk.CTkLabel(self.container, text="CONFIGURAÇÕES", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(0, 15))
        
        self.build_ui()

    def build_ui(self):
        # --- Serial Connection ---
        ctk.CTkLabel(self.container, text="Conexão Serial", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(5, 5))
        
        self.port_optionmenu = ctk.CTkOptionMenu(self.container, variable=self.app.port_var)
        self.port_optionmenu.pack(fill="x", pady=5)
        
        # Populate initial ports
        ports = [p.device for p in serial.tools.list_ports.comports()]
        if not ports: ports = ["Sem portas"]
        self.port_optionmenu.configure(values=ports)

        ctk.CTkButton(self.container, text="Atualizar Portas", command=self.app.refresh_ports, height=24).pack(fill="x", pady=5)

        ctk.CTkLabel(self.container, text="Baud Rate:", font=ctk.CTkFont(size=11)).pack(anchor="w", pady=(5, 0))
        self.baud_optionmenu = ctk.CTkOptionMenu(self.container, variable=self.app.baud_var, values=["9600", "115200", "19200", "38400", "57600"])
        self.baud_optionmenu.pack(fill="x", pady=5)

        # --- Console Toggle ---
        btn_text = "Esconder Console" if self.app.is_console_visible else "Exibir Console"
        self.console_btn = ctk.CTkButton(self.container, text=btn_text, command=self.app.toggle_console, fg_color="#34495e", hover_color="#2c3e50")
        self.console_btn.pack(fill="x", pady=(10, 0))
        
        ctk.CTkButton(self.container, text="Fechar", command=self.destroy, fg_color="#2ecc71", hover_color="#27ae60").pack(pady=(15, 0), fill="x")

class PiParametersModal(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Parâmetros do Controlador PI")
        self.geometry("450x550")
        self.resizable(False, False)
        
        self.transient(app)
        self.grab_set()
        
        self.container = ctk.CTkFrame(self)
        self.container.pack(fill="both", expand=True, padx=20, pady=20)
        
        ctk.CTkLabel(self.container, text="PARÂMETROS DO CONTROLADOR PI", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(10, 20))
        
        self.build_inputs()
        
        # Botões de Ação
        actions_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        actions_frame.pack(fill="x", pady=15)
        
        ctk.CTkButton(actions_frame, text="Zerar Estado (zpi)", fg_color="#e67e22", hover_color="#d35400", command=self.app.send_pi_reset).pack(side="left", fill="x", expand=True, padx=(0, 5))
        ctk.CTkButton(actions_frame, text="Atualizar Info", fg_color="#34495e", hover_color="#2c3e50", command=self.app.request_pi_info).pack(side="right", fill="x", expand=True, padx=(5, 0))
        
        # Estado atual lido do Arduino
        ctk.CTkLabel(self.container, text="ESTADO ATUAL NO ARDUINO", font=ctk.CTkFont(size=12, weight="bold"), text_color="gray").pack(pady=(15, 5))
        
        info_display_frame = ctk.CTkFrame(self.container, fg_color="#1e1e1e", border_width=1, border_color="#2c3e50")
        info_display_frame.pack(fill="both", expand=True, pady=(0, 10))
        
        self.pi_info_labels = {}
        fields = [
            ("Setpoint (SP):", "sp_val", "-"),
            ("Volume V0:", "v0_val", "-"),
            ("Kp / Ki:", "k_val", "- / -"),
            ("Erro Acumulado:", "err_val", "-"),
            ("u_max / u_prev:", "u_val", "- / -")
        ]
        for label_text, key, default in fields:
            row_f = ctk.CTkFrame(info_display_frame, fg_color="transparent")
            row_f.pack(fill="x", padx=15, pady=3)
            ctk.CTkLabel(row_f, text=label_text, font=ctk.CTkFont(size=11, weight="bold"), text_color="gray").pack(side="left")
            val_l = ctk.CTkLabel(row_f, text=default, font=ctk.CTkFont(size=11))
            val_l.pack(side="right")
            self.pi_info_labels[key] = val_l
            
        # Store in app references so app can update them
        self.app.modal_info_labels = self.pi_info_labels
        
        # Populate with current values from app
        self.update_values()
        
        # Close button
        ctk.CTkButton(self.container, text="Fechar", command=self.destroy, fg_color="#34495e", hover_color="#2c3e50").pack(fill="x", pady=(10, 0))
        
    def build_inputs(self):
        self.create_input_row("Ganho Proporcional Kp:", "ex: 1.0", self.app.send_pi_kp, "pi_kp_entry")
        self.create_input_row("Ganho Integral Ki:", "ex: 0.1", self.app.send_pi_ki, "pi_ki_entry")
        self.create_input_row("Vol. Reator V0 (ml):", "ex: 250", self.app.send_pi_v0, "pi_v0_entry")
        self.create_input_row("Intervalo PI (ms):", "ex: 1000", self.app.send_pi_int, "pi_int_entry")
        
    def create_input_row(self, label_text, placeholder, command, attr_name):
        row_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        row_frame.pack(fill="x", pady=4)
        ctk.CTkLabel(row_frame, text=label_text, font=ctk.CTkFont(size=12)).pack(side="left")
        
        entry = ctk.CTkEntry(row_frame, placeholder_text=placeholder, width=100, height=26)
        entry.pack(side="right", padx=(5, 0))
        setattr(self.app, attr_name, entry)
        entry.bind("<Return>", lambda e: command())
        
        btn = ctk.CTkButton(row_frame, text="SET", width=50, height=26, command=command)
        btn.pack(side="right")
        
    def update_values(self):
        sp = self.app.latest_tag_values.get("Setpoint (SP)", "-")
        if isinstance(sp, float): sp = f"{sp:.2f}"
        
        v0 = self.app.latest_tag_values.get("Volume Reator V0", "-")
        kp = getattr(self.app, "current_kp", "-")
        ki = getattr(self.app, "current_ki", "-")
        
        err = self.app.latest_tag_values.get("Erro acumulado (I)", "-")
        if isinstance(err, float): err = f"{err:.4f}"
            
        umax = getattr(self.app, "current_umax", "-")
        uprev = getattr(self.app, "current_uprev", "-")
        
        self.pi_info_labels["sp_val"].configure(text=str(sp))
        self.pi_info_labels["v0_val"].configure(text=str(v0))
        self.pi_info_labels["k_val"].configure(text=f"{kp} / {ki}")
        self.pi_info_labels["err_val"].configure(text=str(err))
        self.pi_info_labels["u_val"].configure(text=f"{umax} / {uprev}")

    def destroy(self):
        if hasattr(self.app, "modal_info_labels"):
            self.app.modal_info_labels = None
        super().destroy()

if __name__ == "__main__":
    app = SerialApp()
    app.mainloop()
