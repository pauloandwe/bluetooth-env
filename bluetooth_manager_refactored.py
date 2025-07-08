import os
import sys
import signal
import logging
from datetime import datetime
from config import Config
from device_service import DeviceService
from web_interface import WebInterface
from serial_handler import SerialHandler

# Configuração de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BluetoothManagerRefactored:
    def __init__(self):
        self.config = Config()
        self.device_service = DeviceService(self.config)
        self.serial_handler = SerialHandler()
        self.web_interface = WebInterface(self.config, self.device_service, self.serial_handler)
        
        # Conectar callback de log do serial handler
        self.serial_handler.set_log_callback(self.device_service.log_message)
        
        self.start_time = datetime.now()
        self.setup_signal_handlers()
    
    def setup_signal_handlers(self):
        def signal_handler(sig, frame):
            self.device_service.log_message("🛑 Encerrando sistema...")
            self.device_service.is_scanning = False
            self.device_service.is_scanning_all = False
            self.serial_handler.close_serial_port()
            sys.exit(0)
            
        signal.signal(signal.SIGINT, signal_handler)
    
    def run(self):
        self.device_service.log_message("🚀 Sistema Bluetooth Manager iniciado")
        self.device_service.log_message(f"🎯 {len(self.config.valid_devices)} endereços MAC autorizados configurados")
        
        print(f"\n🎉 Sistema Bluetooth Manager iniciado!")
        print(f"📱 Interface Web: http://localhost:{self.config.get('web_port')}")
        print(f"🎯 {len(self.config.valid_devices)} endereços MAC autorizados")
        print(f"⚠️  Use Ctrl+C para parar o sistema\n")
        
        # Criar diretório templates se não existir
        os.makedirs('templates', exist_ok=True)
        os.makedirs('static', exist_ok=True)
        
        # Iniciar servidor web
        self.web_interface.run(
            host='0.0.0.0',
            port=self.config.get('web_port'),
            debug=False
        )

if __name__ == "__main__":
    app = BluetoothManagerRefactored()
    app.run()