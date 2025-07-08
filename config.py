import json
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

class Config:
    def __init__(self, config_file: str = 'bluetooth_config.json'):
        self.config_file = config_file
        self.default_config = {
            'socket_port': 8888,
            'web_port': 5001,
            'scan_interval': 5,
            'data_update_interval': 2,
            'connection_timeout': 10,
            'max_connection_attempts': 3,
        }
        self.default_valid_devices = [
            {"address": "DE473C54-A63F-734C-3F67-064A2C6E1DC8", "name": "Bastão 1"},
            {"address": "11:22:33:44:55:66", "name": "Device 2"},
            {"address": "77:88:99:AA:BB:CC", "name": "Device 3"},
            {"address": "2A328859-8CB4-994A-F780-440D72EF1A0E", "name": "Device 4"},
        ]
        
        self.config = self.default_config.copy()
        self.valid_devices = self.default_valid_devices.copy()
        self.valid_mac_addresses = []
        
        self.load_config()
    
    def load_config(self):
        try:
            with open(self.config_file, 'r') as f:
                loaded_config = json.load(f)
                self.config.update(loaded_config)
                
                if 'valid_devices' in loaded_config:
                    self.valid_devices = loaded_config['valid_devices']
                elif 'valid_mac_addresses' in loaded_config:
                    self.valid_devices = [
                        {"address": addr, "name": addr} 
                        for addr in loaded_config['valid_mac_addresses']
                    ]
                
                self.update_valid_mac_addresses()
                logger.info("Configurações carregadas com sucesso")
        except FileNotFoundError:
            self.save_config()
            logger.info("Arquivo de configuração criado")
        except Exception as e:
            logger.error(f"Erro ao carregar configurações: {e}")
    
    def save_config(self):
        try:
            config_to_save = self.config.copy()
            config_to_save['valid_devices'] = self.valid_devices
            
            with open(self.config_file, 'w') as f:
                json.dump(config_to_save, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Erro ao salvar configurações: {e}")
    
    def update_valid_mac_addresses(self):
        self.valid_mac_addresses = [d["address"] for d in self.valid_devices]
    
    def get(self, key: str, default=None):
        return self.config.get(key, default)
    
    def set(self, key: str, value):
        self.config[key] = value
        self.save_config()
    
    def add_valid_device(self, address: str, name: str):
        if address not in self.valid_mac_addresses:
            self.valid_devices.append({"address": address, "name": name})
            self.update_valid_mac_addresses()
            self.save_config()
            return True
        return False
    
    def update_valid_devices(self, devices: List[Dict]):
        self.valid_devices = devices
        self.update_valid_mac_addresses()
        self.save_config()