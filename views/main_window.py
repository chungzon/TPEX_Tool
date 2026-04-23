import customtkinter as ctk

from viewmodels.broker_download_viewmodel import BrokerDownloadViewModel
from viewmodels.batch_download_viewmodel import BatchDownloadViewModel
from viewmodels.broker_analysis_viewmodel import BrokerAnalysisViewModel
from viewmodels.settings_viewmodel import SettingsViewModel
from views.broker_download_view import BrokerDownloadView
from views.batch_download_view import BatchDownloadView
from views.broker_analysis_view import BrokerAnalysisView
from views.settings_view import SettingsView
from services.config_service import ConfigService
from services.scheduler_service import SchedulerService


class MainWindow(ctk.CTk):
    """Application main window with tabbed navigation."""

    APP_TITLE = "TPEX Tool — 上櫃資料工具"
    APP_SIZE = (960, 720)

    def __init__(self):
        super().__init__()

        self.title(self.APP_TITLE)
        self.geometry(f"{self.APP_SIZE[0]}x{self.APP_SIZE[1]}")
        self.minsize(720, 520)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._build_ui()

    def _build_ui(self):
        # --- Header ---
        header = ctk.CTkFrame(self, height=50, corner_radius=0)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header,
            text="TPEX Tool",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left", padx=20)

        # Theme toggle
        self.theme_switch = ctk.CTkSwitch(
            header,
            text="深色模式",
            command=self._toggle_theme,
            onvalue="dark",
            offvalue="light",
        )
        self.theme_switch.pack(side="right", padx=20)
        self.theme_switch.select()

        # --- Tab view ---
        self.tabview = ctk.CTkTabview(self, corner_radius=12)
        self.tabview.pack(fill="both", expand=True, padx=16, pady=(8, 16))

        # Tab 1: 上櫃分點資料下載
        tab1 = self.tabview.add("上櫃分點資料下載")

        self.broker_vm = BrokerDownloadViewModel()
        broker_view = BrokerDownloadView(tab1, self.broker_vm)
        broker_view.pack(fill="both", expand=True)

        # Tab 2: 批次下載至資料庫
        tab2 = self.tabview.add("批次下載至資料庫")

        self.batch_vm = BatchDownloadViewModel()
        batch_view = BatchDownloadView(tab2, self.batch_vm)
        batch_view.pack(fill="both", expand=True)

        # Tab 3: 分點分析
        tab3 = self.tabview.add("分點分析")

        self.analysis_vm = BrokerAnalysisViewModel()
        analysis_view = BrokerAnalysisView(tab3, self.analysis_vm)
        analysis_view.pack(fill="both", expand=True)

        # Tab 4: 系統設定
        tab4 = self.tabview.add("系統設定")

        self._config_svc = ConfigService()
        self._scheduler_svc = SchedulerService(self._config_svc)
        self.settings_vm = SettingsViewModel(self._config_svc, self._scheduler_svc)
        settings_view = SettingsView(tab4, self.settings_vm)
        settings_view.pack(fill="both", expand=True)

        # Cleanup on close
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # --- Status bar ---
        status_bar = ctk.CTkFrame(self, height=28, corner_radius=0)
        status_bar.pack(fill="x", side="bottom")
        status_bar.pack_propagate(False)

        ctk.CTkLabel(
            status_bar,
            text="v1.0.0  |  資料來源：證券櫃檯買賣中心",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(side="left", padx=12)

    def _toggle_theme(self):
        mode = self.theme_switch.get()
        ctk.set_appearance_mode(mode)

    def _on_close(self):
        self.broker_vm.shutdown()
        self.batch_vm.shutdown()
        self.analysis_vm.shutdown()
        self.settings_vm.shutdown()
        self.destroy()
