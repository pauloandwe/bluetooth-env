import asyncio
import threading
import json
import time
import requests
from datetime import datetime, timedelta
from bleak import BleakScanner, BleakClient
import logging
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
import signal
import sys
import os
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional
import platform
import subprocess

# Configuração de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class DeviceInfo:
    address: str
    name: str
    last_seen: str
    connected: bool = False
    rssi: Optional[int] = None
    connection_attempts: int = 0
    is_authorized: bool = False
    device_type: str = "Unknown"

@dataclass
class LogEntry:
    timestamp: str
    level: str
    message: str
    device_address: Optional[str] = None

class BluetoothManager:
    def __init__(self):
        # Lista de dispositivos autorizados contendo nome e endereço
        self.valid_devices = [
            {"address": "AA:BB:CC:DD:EE:FF", "name": "Device 1"},
            {"address": "11:22:33:44:55:66", "name": "Device 2"},
            {"address": "77:88:99:AA:BB:CC", "name": "Device 3"},
            {"address": "2A328859-8CB4-994A-F780-440D72EF1A0E", "name": "Device 4"},
        ]
        # Lista simples de MACs para verificações rápidas
        self.update_valid_mac_addresses()
        
        self.detected_devices: Dict[str, DeviceInfo] = {}
        self.all_devices: Dict[str, DeviceInfo] = {}  # Todos os dispositivos encontrados
        self.device_data: Dict[str, Dict] = {}
        self.is_scanning = False
        self.is_scanning_all = False
        self.is_connecting = False
        self.logs: List[LogEntry] = []
        self.socketio = None
        
        self.config = {
            'socket_port': 8888,
            'web_port': 5001,
            'scan_interval': 5,
            'data_update_interval': 2,
            'connection_timeout': 10,
            'max_connection_attempts': 3,
        }
        
        self.setup_flask()
        self.load_config()

    def update_valid_mac_addresses(self):
        """Atualiza lista simples de endereços MAC a partir dos dispositivos válidos"""
        self.valid_mac_addresses = [d["address"] for d in self.valid_devices]
        
    def load_config(self):
        """Carrega configurações do arquivo JSON"""
        try:
            with open('bluetooth_config.json', 'r') as f:
                loaded_config = json.load(f)
                self.config.update(loaded_config)
                if 'valid_devices' in loaded_config:
                    self.valid_devices = loaded_config['valid_devices']
                elif 'valid_mac_addresses' in loaded_config:
                    self.valid_devices = [
                        {"address": addr, "name": addr} for addr in loaded_config['valid_mac_addresses']
                    ]
                self.update_valid_mac_addresses()
                self.log_message("Configurações carregadas com sucesso", "INFO")
        except FileNotFoundError:
            self.save_config()
            self.log_message("Arquivo de configuração criado", "INFO")
        except Exception as e:
            self.log_message(f"Erro ao carregar configurações: {e}", "ERROR")
            
    def save_config(self):
        """Salva configurações no arquivo JSON"""
        try:
            config_to_save = self.config.copy()
            config_to_save['valid_devices'] = self.valid_devices
            
            with open('bluetooth_config.json', 'w') as f:
                json.dump(config_to_save, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.log_message(f"Erro ao salvar configurações: {e}", "ERROR")
            
    def setup_flask(self):
        """Configura Flask e rotas"""
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = 'bluetooth_manager_2024'
        self.socketio = SocketIO(self.app, cors_allowed_origins="*", async_mode='threading')
        
        self.setup_routes()
        self.setup_socket_events()
        
    def setup_routes(self):
        """Define todas as rotas da API"""
        
        @self.app.route('/')
        def index():
            return render_template('index.html')
            
        @self.app.route('/api/status')
        def api_status():
            return jsonify({
                'is_scanning': self.is_scanning,
                'is_scanning_all': self.is_scanning_all,
                'is_connecting': self.is_connecting,
                'valid_devices': self.valid_devices,
                'detected_devices': {addr: asdict(device) for addr, device in self.detected_devices.items()},
                'all_devices': {addr: asdict(device) for addr, device in self.all_devices.items()},
                'device_data': self.device_data,
                'logs': [asdict(log) for log in self.logs[-100:]],
                'stats': self.get_system_stats(),
                'connected_system_devices': self.get_connected_system_devices()
            })
            
        @self.app.route('/api/start_scan', methods=['POST'])
        def api_start_scan():
            if not self.is_scanning:
                self.start_scanning()
                return jsonify({'success': True, 'message': 'Escaneamento de dispositivos autorizados iniciado'})
            return jsonify({'success': False, 'message': 'Já está escaneando'})
            
        @self.app.route('/api/start_scan_all', methods=['POST'])
        def api_start_scan_all():
            if not self.is_scanning_all:
                self.start_scanning_all()
                return jsonify({'success': True, 'message': 'Escaneamento completo iniciado'})
            return jsonify({'success': False, 'message': 'Já está escaneando todos os dispositivos'})
            
        @self.app.route('/api/stop_scan', methods=['POST'])
        def api_stop_scan():
            self.stop_scanning()
            return jsonify({'success': True, 'message': 'Escaneamento parado'})
            
        @self.app.route('/api/connect_device/<device_address>', methods=['POST'])
        def api_connect_device(device_address):
            success = self.connect_single_device(device_address)
            if success:
                return jsonify({'success': True, 'message': f'Conectando dispositivo {device_address}'})
            return jsonify({'success': False, 'message': 'Erro ao conectar dispositivo'})
            
        @self.app.route('/api/disconnect_device/<device_address>', methods=['POST'])
        def api_disconnect_device(device_address):
            self.disconnect_single_device(device_address)
            return jsonify({'success': True, 'message': f'Dispositivo {device_address} desconectado'})
            
        @self.app.route('/api/connect_all', methods=['POST'])
        def api_connect_all():
            self.connect_all_devices()
            return jsonify({'success': True, 'message': 'Conectando todos os dispositivos válidos'})
            
        @self.app.route('/api/disconnect_all', methods=['POST'])
        def api_disconnect_all():
            self.disconnect_all_devices()
            return jsonify({'success': True, 'message': 'Todos os dispositivos desconectados'})
            
        @self.app.route('/api/clear_logs', methods=['POST'])
        def api_clear_logs():
            self.logs.clear()
            self.log_message("Logs limpos", "INFO")
            return jsonify({'success': True, 'message': 'Logs limpos'})
            
        @self.app.route('/api/update_valid_macs', methods=['POST'])
        def api_update_valid_macs():
            data = request.json
            if 'devices' in data and isinstance(data['devices'], list):
                self.valid_devices = data['devices']
                self.update_valid_mac_addresses()
                self.save_config()
                self.log_message(
                    f"Lista de dispositivos válidos atualizada: {len(self.valid_devices)} endereços",
                    "INFO")
                return jsonify({'success': True, 'message': 'Lista de dispositivos atualizada'})
            elif 'mac_addresses' in data and isinstance(data['mac_addresses'], list):
                # Suporte legado - apenas endereços
                self.valid_devices = [{"address": addr, "name": addr} for addr in data['mac_addresses']]
                self.update_valid_mac_addresses()
                self.save_config()
                return jsonify({'success': True, 'message': 'Lista atualizada'})
            return jsonify({'success': False, 'message': 'Formato inválido'})
            
        @self.app.route('/api/add_to_whitelist/<device_address>', methods=['POST'])
        def api_add_to_whitelist(device_address):
            if device_address not in self.valid_mac_addresses:
                device_name = None
                if device_address in self.all_devices:
                    device_name = self.all_devices[device_address].name
                payload = request.json or {}
                device_name = payload.get('name') or device_name or device_address

                self.valid_devices.append({"address": device_address, "name": device_name})
                self.update_valid_mac_addresses()
                self.save_config()

                # Mover dispositivo para lista autorizada se existir
                if device_address in self.all_devices:
                    device = self.all_devices[device_address]
                    device.is_authorized = True
                    device.name = device_name
                    self.detected_devices[device_address] = device

                self.log_message(f"Dispositivo {device_address} adicionado à whitelist", "INFO")
                return jsonify({'success': True, 'message': 'Dispositivo adicionado à whitelist'})
            return jsonify({'success': False, 'message': 'Dispositivo já está na whitelist'})
        
    def setup_socket_events(self):
        """Configura eventos do SocketIO"""
        
        @self.socketio.on('connect')
        def handle_connect():
            self.log_message('Cliente web conectado', "INFO")
            emit('initial_data', {
                'is_scanning': self.is_scanning,
                'is_scanning_all': self.is_scanning_all,
                'is_connecting': self.is_connecting,
                'valid_devices': self.valid_devices,
                'detected_devices': {addr: asdict(device) for addr, device in self.detected_devices.items()},
                'all_devices': {addr: asdict(device) for addr, device in self.all_devices.items()},
                'device_data': self.device_data,
                'logs': [asdict(log) for log in self.logs[-50:]],
                'stats': self.get_system_stats(),
                'connected_system_devices': self.get_connected_system_devices()
            })
            
        @self.socketio.on('disconnect')
        def handle_disconnect():
            self.log_message('Cliente web desconectado', "INFO")
            
    def get_connected_system_devices(self):
        """Obtém dispositivos Bluetooth conectados ao sistema"""
        connected_devices = []
        try:
            if platform.system() == "Windows":
                # Windows PowerShell command
                result = subprocess.run([
                    'powershell', '-Command',
                    'Get-PnpDevice -Class Bluetooth | Where-Object {$_.Status -eq "OK"} | Select-Object Name, InstanceId'
                ], capture_output=True, text=True, timeout=10)
                
                if result.returncode == 0:
                    lines = result.stdout.strip().split('\n')[3:]  # Skip headers
                    for line in lines:
                        if line.strip():
                            parts = line.split()
                            if len(parts) >= 2:
                                name = ' '.join(parts[:-1])
                                connected_devices.append({
                                    'name': name.strip(),
                                    'status': 'Connected to System'
                                })
                                
            elif platform.system() == "Linux":
                # Linux bluetoothctl command
                result = subprocess.run(['bluetoothctl', 'devices', 'Connected'], 
                                      capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    for line in result.stdout.strip().split('\n'):
                        if 'Device' in line:
                            parts = line.split(' ', 2)
                            if len(parts) >= 3:
                                address = parts[1]
                                name = parts[2]
                                connected_devices.append({
                                    'address': address,
                                    'name': name,
                                    'status': 'Connected to System'
                                })
                                
            elif platform.system() == "Darwin":  # macOS
                # macOS system_profiler command
                result = subprocess.run(['system_profiler', 'SPBluetoothDataType'], 
                                      capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    # Parse system_profiler output (simplified)
                    lines = result.stdout.split('\n')
                    for line in lines:
                        if 'Connected: Yes' in line:
                            # Extract device info from previous lines
                            connected_devices.append({
                                'name': 'Connected Bluetooth Device',
                                'status': 'Connected to System'
                            })
                            
        except subprocess.TimeoutExpired:
            self.log_message("Timeout ao buscar dispositivos conectados do sistema", "WARNING")
        except Exception as e:
            self.log_message(f"Erro ao obter dispositivos conectados do sistema: {e}", "ERROR")
            
        return connected_devices

    def log_message(self, message, level="INFO", device_address=None):
        """Adiciona mensagem aos logs"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = LogEntry(
            timestamp=timestamp,
            level=level,
            message=message,
            device_address=device_address
        )
        
        self.logs.append(log_entry)
        
        if len(self.logs) > 500:
            self.logs = self.logs[-500:]
            
        if level == "ERROR":
            logger.error(message)
        elif level == "WARNING":
            logger.warning(message)
        else:
            logger.info(message)
        
        if self.socketio:
            try:
                self.socketio.emit('log_update', asdict(log_entry))
            except Exception as e:
                logger.error(f"Erro ao enviar log via socketio: {e}")
        
    def get_system_stats(self):
        """Obtém estatísticas do sistema"""
        now = datetime.now()
        connected_count = sum(1 for device in self.detected_devices.values() if device.connected)
        all_connected_count = sum(1 for device in self.all_devices.values() if device.connected)
        
        return {
            'valid_addresses_count': len(self.valid_devices),
            'detected_devices': len(self.detected_devices),
            'all_devices_count': len(self.all_devices),
            'connected_devices': connected_count,
            'all_connected_devices': all_connected_count,
            'uptime': str(now - getattr(self, 'start_time', now)),
        }
        
    async def scan_devices(self, authorized_only=True):
        """Escaneia dispositivos Bluetooth"""
        while (self.is_scanning and authorized_only) or (self.is_scanning_all and not authorized_only):
            try:
                scan_type = "autorizados" if authorized_only else "todos os"
                self.log_message(f"🔍 Escaneando {scan_type} dispositivos Bluetooth...")
                
                devices = await BleakScanner.discover(timeout=8.0, return_adv=True)
                current_time = datetime.now()
                found_devices = []
                new_devices = 0
                
                for device_address, (device, adv_data) in devices.items():
                    is_authorized = device_address in self.valid_mac_addresses
                    stored_name = None
                    if is_authorized:
                        for d in self.valid_devices:
                            if d['address'] == device_address:
                                stored_name = d.get('name')
                                break

                    # Filtrar por tipo de scan
                    if authorized_only and not is_authorized:
                        continue

                    device_info = DeviceInfo(
                        address=device_address,
                        name=stored_name or device.name or f"Dispositivo {device_address[-5:]}",
                        last_seen=current_time.isoformat(),
                        connected=False,
                        rssi=getattr(adv_data, 'rssi', None),
                        is_authorized=is_authorized
                    )
                    
                    found_devices.append(device_info)
                    
                    # Adicionar aos dicionários apropriados
                    target_dict = self.detected_devices if authorized_only else self.all_devices
                    
                    if device_address not in target_dict:
                        target_dict[device_address] = device_info
                        new_devices += 1
                        
                        status_icon = "✅" if is_authorized else "📱"
                        auth_text = "autorizado" if is_authorized else "detectado"
                        self.log_message(
                            f"{status_icon} Dispositivo {auth_text}: {device_address} ({device_info.name}) - RSSI: {device_info.rssi}")
                    else:
                        # Atualizar informações
                        existing_device = target_dict[device_address]
                        existing_device.last_seen = current_time.isoformat()
                        existing_device.rssi = device_info.rssi
                        existing_device.name = device_info.name
                
                if new_devices > 0:
                    device_type = "autorizados" if authorized_only else "novos"
                    self.log_message(f"🎯 {new_devices} {device_type} dispositivos encontrados")
                
                # Enviar atualizações para interface web
                if self.socketio:
                    try:
                        update_data = {
                            'found_devices': [asdict(device) for device in found_devices],
                            'stats': self.get_system_stats()
                        }
                        
                        if authorized_only:
                            update_data['detected_devices'] = {addr: asdict(device) for addr, device in self.detected_devices.items()}
                            self.socketio.emit('devices_update', update_data)
                        else:
                            update_data['all_devices'] = {addr: asdict(device) for addr, device in self.all_devices.items()}
                            self.socketio.emit('all_devices_update', update_data)
                            
                    except Exception as e:
                        logger.error(f"Erro ao enviar atualização de dispositivos: {e}")
                            
                await asyncio.sleep(self.config['scan_interval'])
                
            except Exception as e:
                self.log_message(f"❌ Erro durante escaneamento: {e}", "ERROR")
                await asyncio.sleep(3)
                
    def start_scanning(self):
        """Inicia escaneamento de dispositivos autorizados"""
        if not self.is_scanning:
            self.is_scanning = True
            self.log_message("🚀 Escaneamento de dispositivos autorizados iniciado")
            self._run_scan_thread(authorized_only=True)
            
    def start_scanning_all(self):
        """Inicia escaneamento de todos os dispositivos"""
        if not self.is_scanning_all:
            self.is_scanning_all = True
            self.log_message("🚀 Escaneamento completo iniciado")
            self._run_scan_thread(authorized_only=False)
            
    def _run_scan_thread(self, authorized_only=True):
        """Executa thread de escaneamento"""
        def run_scan():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.scan_devices(authorized_only))
            except Exception as e:
                self.log_message(f"Erro no loop de escaneamento: {e}", "ERROR")
            finally:
                loop.close()
            
        scan_thread = threading.Thread(target=run_scan)
        scan_thread.daemon = True
        scan_thread.start()
        
        if self.socketio:
            try:
                event_name = 'scanning_status' if authorized_only else 'scanning_all_status'
                self.socketio.emit(event_name, {'is_scanning': True})
            except Exception as e:
                logger.error(f"Erro ao emitir status de escaneamento: {e}")
            
    def stop_scanning(self):
        """Para todos os tipos de escaneamento"""
        self.is_scanning = False
        self.is_scanning_all = False
        self.log_message("⏹️ Escaneamento parado")
        
        if self.socketio:
            try:
                self.socketio.emit('scanning_status', {'is_scanning': False})
                self.socketio.emit('scanning_all_status', {'is_scanning': False})
            except Exception as e:
                logger.error(f"Erro ao emitir status de escaneamento: {e}")
        
    def connect_single_device(self, device_address):
        """Conecta um dispositivo específico"""
        def connect():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.connect_device(device_address))
            finally:
                loop.close()
                
        thread = threading.Thread(target=connect)
        thread.daemon = True
        thread.start()
        return True
        
    def disconnect_single_device(self, device_address):
        """Desconecta um dispositivo específico"""
        # Atualizar em ambos os dicionários
        for device_dict in [self.detected_devices, self.all_devices]:
            if device_address in device_dict:
                device_dict[device_address].connected = False
                device_dict[device_address].connection_attempts = 0
                
        if device_address in self.device_data:
            del self.device_data[device_address]
            
        self.log_message(f"🔌 Dispositivo {device_address} desconectado", "INFO")
        
        if self.socketio:
            try:
                self.socketio.emit('device_disconnected', {'device_address': device_address})
            except Exception as e:
                logger.error(f"Erro ao emitir desconexão de dispositivo: {e}")
            
    async def connect_device(self, device_address):
        """Conecta a um dispositivo específico"""
        try:
            # Procurar dispositivo em ambas as listas
            device_info = (self.detected_devices.get(device_address) or 
                          self.all_devices.get(device_address))
            
            if not device_info:
                self.log_message(f"❌ Dispositivo {device_address} não foi detectado ainda", "WARNING")
                return False
                
            if device_info.connection_attempts >= self.config['max_connection_attempts']:
                self.log_message(f"❌ Máximo de tentativas de conexão atingido para {device_address}", "WARNING")
                return False
                
            device_info.connection_attempts += 1
            self.log_message(f"🔗 Tentativa {device_info.connection_attempts}: Conectando ao {device_address}")
            
            self.is_connecting = True
            
            try:
                async with BleakClient(device_address, timeout=self.config['connection_timeout']) as client:
                    if client.is_connected:
                        device_info.connected = True
                        device_info.connection_attempts = 0
                        self.log_message(f"✅ Conectado ao dispositivo: {device_address}")
                        
                        if self.socketio:
                            try:
                                self.socketio.emit('device_connected', {
                                    'device_address': device_address,
                                    'device_name': device_info.name
                                })
                            except Exception as e:
                                logger.error(f"Erro ao emitir conexão de dispositivo: {e}")
                        
                        return True
                        
            except asyncio.TimeoutError:
                self.log_message(f"⏰ Timeout ao conectar com {device_address}", "WARNING")
            except Exception as e:
                self.log_message(f"❌ Erro ao conectar com {device_address}: {e}", "ERROR")
                
        except Exception as e:
            self.log_message(f"❌ Erro geral na conexão com {device_address}: {e}", "ERROR")
        finally:
            self.is_connecting = False
            
        return False
        
    def connect_all_devices(self):
        """Conecta todos os dispositivos autorizados"""
        def connect_all():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            disconnected_devices = [addr for addr, device in self.detected_devices.items() 
                                  if not device.connected]
            
            if not disconnected_devices:
                self.log_message("ℹ️ Todos os dispositivos válidos já estão conectados")
                return
                
            self.log_message(f"🔗 Conectando {len(disconnected_devices)} dispositivos válidos...")
            
            tasks = [self.connect_device(addr) for addr in disconnected_devices]
                    
            if tasks:
                try:
                    loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                except Exception as e:
                    self.log_message(f"Erro ao conectar dispositivos: {e}", "ERROR")
            
            loop.close()
                
        thread = threading.Thread(target=connect_all)
        thread.daemon = True
        thread.start()
        
    def disconnect_all_devices(self):
        """Desconecta todos os dispositivos"""
        disconnected_count = 0
        
        # Desconectar de ambas as listas
        for device_dict in [self.detected_devices, self.all_devices]:
            for device in device_dict.values():
                if device.connected:
                    device.connected = False
                    device.connection_attempts = 0
                    disconnected_count += 1
                
        self.device_data.clear()
        self.log_message(f"🔌 {disconnected_count} dispositivos desconectados")
        
        if self.socketio:
            try:
                self.socketio.emit('all_devices_disconnected', {
                    'disconnected_count': disconnected_count
                })
            except Exception as e:
                logger.error(f"Erro ao emitir desconexão de todos os dispositivos: {e}")
        
    def run(self):
        """Executa o sistema"""
        self.start_time = datetime.now()
        self.log_message("🚀 Sistema Bluetooth Manager iniciado")
        self.log_message(f"🎯 {len(self.valid_devices)} endereços MAC autorizados configurados")
        
        def signal_handler(sig, frame):
            self.log_message("🛑 Encerrando sistema...")
            self.is_scanning = False
            self.is_scanning_all = False
            sys.exit(0)
            
        signal.signal(signal.SIGINT, signal_handler)
        
        print(f"\n🎉 Sistema Bluetooth Manager iniciado!")
        print(f"📱 Interface Web: http://localhost:{self.config['web_port']}")
        print(f"🎯 {len(self.valid_devices)} endereços MAC autorizados")
        print(f"⚠️  Use Ctrl+C para parar o sistema\n")
        
        # Criar diretório templates se não existir
        os.makedirs('templates', exist_ok=True)
        
        # Iniciar servidor web
        self.socketio.run(self.app, 
                         host='0.0.0.0', 
                         port=self.config['web_port'], 
                         debug=False,
                         allow_unsafe_werkzeug=True)

if __name__ == "__main__":
    app = BluetoothManager()
    app.run()