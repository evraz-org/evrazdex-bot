import os
import time
import webbrowser
from threading import Thread

from grapheneapi.exceptions import NumRetriesReached
from PyQt5.QtCore import pyqtSlot
from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import QMainWindow, QApplication, QPushButton
from PyQt5 import QtCore

from dexbot import __version__
from dexbot.config import Config
from dexbot.controllers.wallet_controller import WalletController
from dexbot.qt_queue.idle_queue import idle_add
from dexbot.qt_queue.queue_dispatcher import ThreadDispatcher
from dexbot.views.create_wallet import CreateWalletView
from dexbot.views.create_worker import CreateWorkerView
from dexbot.views.errors import gui_error
from dexbot.views.layouts.flow_layout import FlowLayout
from dexbot.views.settings import SettingsView
from dexbot.views.ui.worker_list_window_ui import Ui_MainWindow
from dexbot.views.unlock_wallet import UnlockWalletView
from dexbot.views.worker_item import WorkerItemWidget
from dexbot.translator_strings import TranslatorStrings as TS

import dexbot.resources

TRANSLATE_DIR = os.path.join(os.path.dirname(dexbot.resources.__file__), 'translates')

TRANSLATE_BN_NORMAL = 'QPushButton {border: 0px; background-color: #424242; width: 50px; height: 20px; border-radius: 5px; color: #ffffff;}'
TRANSLATE_BN_SELECTED = 'QPushButton {border: 0px; background-color: #ffffff; width: 50px; height: 20px; border-radius: 5px; color: black;}'

class MainView(QMainWindow, Ui_MainWindow):
    def __init__(self, main_controller):
        super().__init__()
        self.setupUi(self)
        self.main_controller = main_controller

        self.config = main_controller.config
        self.max_workers = 10
        self.num_of_workers = 0
        self.worker_widgets = {}
        self.closing = False
        self.status_bar_updater = None
        self.statusbar_updater_first_run = True
        self.main_controller.set_info_handler(self.set_worker_status)
        self.layout = FlowLayout(self.scrollAreaContent)
        self.dispatcher = None

        # GUI buttons
        self.add_worker_button.clicked.connect(self.handle_add_worker)
        self.settings_button.clicked.connect(self.handle_open_settings)
        self.help_button.clicked.connect(self.handle_open_documentation)
        self.unlock_wallet_button.clicked.connect(self.handle_login)

        # Hide certain buttons by default until login success
        self.add_worker_button.hide()
        self.widget_5.layout().removeWidget(self.add_worker_button)

        self.translate('ru')
        self.status_bar.showMessage(TS.worker_list[0].format(__version__)) # ver {} - Node disconnected

        QFontDatabase.addApplicationFont(":/bot_widget/font/SourceSansPro-Bold.ttf")

        self.enButton = QPushButton("en")
        self.enButton.setStyleSheet(TRANSLATE_BN_NORMAL)
        self.enButton.clicked.connect(self.translate_en)
        self.status_bar.addPermanentWidget(self.enButton)

        self.ruButton = QPushButton("ru")
        self.ruButton.setStyleSheet(TRANSLATE_BN_SELECTED)
        self.ruButton.clicked.connect(self.translate_ru)
        self.status_bar.addPermanentWidget(self.ruButton)

    def translate(self, current_lang):
        app = QApplication.instance()
        if app is None:
            # if it does not exist then a QApplication is created
            app = QApplication([])

        if hasattr(self, 'translators'):
            for e in self.translators:
                app.removeTranslator(e)

        try:
            file_list:list[str] = [os.path.join(TRANSLATE_DIR, current_lang, e) for e in os.listdir(os.path.join(TRANSLATE_DIR, current_lang))] 

            self.translators = []
            for f in file_list:
                self.translators.append(QtCore.QTranslator())
                self.translators[-1].load(f)

            for e in self.translators:
                app.installTranslator(e)
        except:
            if current_lang != "en":
                print("Error: Can't load translations!")

        self.retranslateUi(self)
        TS.retranslate()
        self.status_bar.showMessage(self.get_statusbar_message())

    def translate_en(self):
        self.translate('en')
        self.enButton.setStyleSheet(TRANSLATE_BN_SELECTED)
        self.ruButton.setStyleSheet(TRANSLATE_BN_NORMAL)

    def translate_ru(self):
        self.translate('ru')
        self.ruButton.setStyleSheet(TRANSLATE_BN_SELECTED)
        self.enButton.setStyleSheet(TRANSLATE_BN_NORMAL)

    def connect_to_bitshares(self):
        # Check if there is already a connection
        if self.config['node']:
            # Test nodes first. This only checks if we're able to connect
            self.status_bar.showMessage(TS.worker_list[1]) # Connecting to Bitshares...
            try:
                self.main_controller.measure_latency(self.config['node'])
            except NumRetriesReached:
                self.status_bar.showMessage(TS.worker_list[2].format(__version__)) # ver {} - Coudn\'t connect to Bitshares. Please use different node(s) and retry.
                self.main_controller.set_bitshares_instance(None)
                return False

            self.main_controller.new_bitshares_instance(self.config['node'])
            self.status_bar.showMessage(self.get_statusbar_message())
            return True
        else:
            # Config has no nodes in it
            self.status_bar.showMessage(TS.worker_list[3].format(__version__)) # ver {} - Node(s) not found. Please add node(s) from settings.
            return False

    @pyqtSlot(name='handle_login')
    def handle_login(self):
        if not self.main_controller.bitshares_instance:
            if not self.connect_to_bitshares():
                return

        wallet_controller = WalletController(self.main_controller.bitshares_instance)

        if wallet_controller.wallet_created():
            unlock_view = UnlockWalletView(wallet_controller)
        else:
            unlock_view = CreateWalletView(wallet_controller)

        if unlock_view.exec_():
            # Hide button once successful wallet creation / login
            self.unlock_wallet_button.hide()
            self.widget_5.layout().removeWidget(self.unlock_wallet_button)

            self.widget_5.layout().addWidget(self.add_worker_button)
            self.add_worker_button.show()

            # Load worker widgets from config file
            workers = self.config.workers_data
            for worker_name in workers:
                self.add_worker_widget(worker_name)

                # Limit the max amount of workers so that the performance isn't greatly affected
                if self.num_of_workers >= self.max_workers:
                    self.add_worker_button.setEnabled(False)
                    break

            # Dispatcher polls for events from the workers that are used to change the ui
            self.dispatcher = ThreadDispatcher(self)
            self.dispatcher.start()

            self.status_bar.showMessage(TS.worker_list[4].format(__version__)) # ver {} - Node delay: - ms
            self.status_bar_updater = Thread(target=self._update_statusbar_message)
            self.status_bar_updater.start()

    def add_worker_widget(self, worker_name):
        config = self.config.get_worker_config(worker_name)
        widget = WorkerItemWidget(worker_name, config, self.main_controller, self)
        widget.setFixedSize(widget.frameSize())
        self.layout.addWidget(widget)
        self.worker_widgets[worker_name] = widget

        # Limit the max amount of workers so that the performance isn't greatly affected
        self.num_of_workers += 1
        if self.num_of_workers >= self.max_workers:
            self.add_worker_button.setEnabled(False)

    def remove_worker_widget(self, worker_name):
        self.worker_widgets.pop(worker_name, None)

        self.num_of_workers -= 1
        if self.num_of_workers < self.max_workers:
            self.add_worker_button.setEnabled(True)

    def change_worker_widget_name(self, old_worker_name, new_worker_name):
        worker_data = self.worker_widgets.pop(old_worker_name)
        self.worker_widgets[new_worker_name] = worker_data

    @pyqtSlot(name='handle_add_worker')
    @gui_error
    def handle_add_worker(self):
        create_worker_dialog = CreateWorkerView(self.main_controller.bitshares_instance)
        return_value = create_worker_dialog.exec_()

        # User clicked save
        if return_value == 1:
            worker_name = create_worker_dialog.worker_name
            self.main_controller.create_worker(worker_name)

            self.config.add_worker_config(worker_name, create_worker_dialog.worker_data)
            self.add_worker_widget(worker_name)

    @pyqtSlot(name='handle_open_settings')
    @gui_error
    def handle_open_settings(self):
        settings_dialog = SettingsView()
        reconnect = settings_dialog.exec_()

        if reconnect:
            # Reinitialize config after closing the settings window
            self.config = Config()
            self.main_controller.config = self.config

            self.connect_to_bitshares()

    @staticmethod
    @pyqtSlot(name='handle_open_documentation')
    def handle_open_documentation():
        webbrowser.open('https://github.com/evraz-org/evrazdex-bot/wiki')

    def set_worker_name(self, worker_name, value):
        self.worker_widgets[worker_name].set_worker_name(value)

    def set_worker_account(self, worker_name, value):
        self.worker_widgets[worker_name].set_worker_account(value)

    def set_worker_profit(self, worker_name, value):
        self.worker_widgets[worker_name].set_worker_profit(value)

    def set_worker_market(self, worker_name, value):
        self.worker_widgets[worker_name].set_worker_market(value)

    def set_worker_slider(self, worker_name, value):
        self.worker_widgets[worker_name].set_worker_slider(value)

    def customEvent(self, event):
        # Process idle_queue_dispatcher events
        event.callback()

    def closeEvent(self, event):
        self.closing = True
        self.status_bar.showMessage(TS.worker_list[5]) # Closing app...
        if self.status_bar_updater and self.status_bar_updater.is_alive():
            self.status_bar_updater.join()

    def _update_statusbar_message(self):
        while not self.closing:
            # When running first time the workers are also interrupting with the connection
            # so we delay the first time to get correct information
            if self.statusbar_updater_first_run:
                self.statusbar_updater_first_run = False
                time.sleep(1)

            msg = self.get_statusbar_message()
            idle_add(self.set_statusbar_message, msg)
            runner_count = 0
            # Wait for 30s but do it in 0.5s pieces to not prevent closing the app
            while not self.closing and runner_count < 60:
                runner_count += 1
                time.sleep(0.5)

    def get_statusbar_message(self):
        try:
            node = self.main_controller.bitshares_instance.rpc.url
            latency = self.main_controller.measure_latency(node)
        except BaseException:
            latency = -1

        if latency != -1:
            return TS.worker_list[6].format(__version__, latency, node) # ver {} - Node delay: {:.2f}ms - node: {}
        else:
            return TS.worker_list[0].format(__version__) # ver {} - Node disconnected

    def set_statusbar_message(self, msg):
        self.status_bar.showMessage(msg)

    def set_worker_status(self, worker_name, level, status):
        if worker_name != 'NONE':
            worker = self.worker_widgets.get(worker_name, None)
            if worker:
                worker.set_status(status)
