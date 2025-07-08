import asyncio
import threading
import platform
import subprocess
from dataclasses import asdict
from typing import List
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
from config import Config
from device_service import DeviceService
from serial_handler import SerialHandler

class WebInterface:
    def __init__(self, config: Config, device_service: DeviceService, serial_handler: SerialHandler):
        self.config = config
        self.device_service = device_service
        self.serial_handler = serial_handler
        
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = 'bluetooth_manager_2024'
        self.socketio = SocketIO(self.app, cors_allowed_origins="*", async_mode='threading')
        
        # Conectar device_service ao socketio
        self.device_service.set_socketio(self.socketio)
        
        self.setup_routes()
        self.setup_socket_events()
    
    def setup_routes(self):
        @self.app.route('/')
        def index():
            return render_template('index_refactored.html')
            
        @self.app.route('/api/status')
        def api_status():
            return jsonify({
                'is_scanning': self.device_service.is_scanning,
                'is_scanning_all': self.device_service.is_scanning_all,
                'is_connecting': self.device_service.is_connecting,
                'valid_devices': self.config.valid_devices,
                'detected_devices': {addr: asdict(device) for addr, device in self.device_service.detected_devices.items()},
                'all_devices': {addr: asdict(device) for addr, device in self.device_service.all_devices.items()},
                'device_data': self.device_service.device_data,
                'logs': [asdict(log) for log in self.device_service.logs[-100:]],
                'stats': self.device_service.get_system_stats(),
                'connected_system_devices': self.get_connected_system_devices()
            })
            
        @self.app.route('/api/start_scan', methods=['POST'])
        def api_start_scan():
            if not self.device_service.is_scanning:
                self.start_scanning()
                return jsonify({'success': True, 'message': 'Escaneamento de dispositivos autorizados iniciado'})
            return jsonify({'success': False, 'message': 'Já está escaneando'})
            
        @self.app.route('/api/start_scan_all', methods=['POST'])
        def api_start_scan_all():
            if not self.device_service.is_scanning_all:
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
            self.device_service.disconnect_device(device_address)
            return jsonify({'success': True, 'message': f'Dispositivo {device_address} desconectado'})
            
        @self.app.route('/api/connect_all', methods=['POST'])
        def api_connect_all():
            self.connect_all_devices()
            return jsonify({'success': True, 'message': 'Conectando todos os dispositivos válidos'})
            
        @self.app.route('/api/disconnect_all', methods=['POST'])
        def api_disconnect_all():
            self.device_service.disconnect_all_devices()
            return jsonify({'success': True, 'message': 'Todos os dispositivos desconectados'})
            
        @self.app.route('/api/clear_logs', methods=['POST'])
        def api_clear_logs():
            self.device_service.clear_logs()
            return jsonify({'success': True, 'message': 'Logs limpos'})
            
        @self.app.route('/api/update_valid_macs', methods=['POST'])
        def api_update_valid_macs():
            data = request.json
            if 'devices' in data and isinstance(data['devices'], list):
                self.config.update_valid_devices(data['devices'])
                self.device_service.log_message(
                    f"Lista de dispositivos válidos atualizada: {len(self.config.valid_devices)} endereços",
                    "INFO")
                return jsonify({'success': True, 'message': 'Lista de dispositivos atualizada'})
            elif 'mac_addresses' in data and isinstance(data['mac_addresses'], list):
                devices = [{"address": addr, "name": addr} for addr in data['mac_addresses']]
                self.config.update_valid_devices(devices)
                return jsonify({'success': True, 'message': 'Lista atualizada'})
            return jsonify({'success': False, 'message': 'Formato inválido'})
            
        @self.app.route('/api/add_to_whitelist/<device_address>', methods=['POST'])
        def api_add_to_whitelist(device_address):
            if device_address not in self.config.valid_mac_addresses:
                device_name = None
                if device_address in self.device_service.all_devices:
                    device_name = self.device_service.all_devices[device_address].name
                payload = request.json or {}
                device_name = payload.get('name') or device_name or device_address

                if self.config.add_valid_device(device_address, device_name):
                    # Mover dispositivo para lista autorizada se existir
                    if device_address in self.device_service.all_devices:
                        device = self.device_service.all_devices[device_address]
                        device.is_authorized = True
                        device.name = device_name
                        self.device_service.detected_devices[device_address] = device

                    self.device_service.log_message(f"Dispositivo {device_address} adicionado à whitelist", "INFO")
                    return jsonify({'success': True, 'message': 'Dispositivo adicionado à whitelist'})
            return jsonify({'success': False, 'message': 'Dispositivo já está na whitelist'})

        # Rotas para serial
        @self.app.route('/api/serial_ports')
        def api_serial_ports():
            ports = [asdict(p) for p in self.serial_handler.list_serial_ports()]
            return jsonify({'ports': ports})

        @self.app.route('/api/open_serial', methods=['POST'])
        def api_open_serial():
            data = request.json or {}
            port = data.get('port')
            baudrate = data.get('baudrate', 9600)
            if not port:
                return jsonify({'success': False, 'message': 'Porta não especificada'})
            if self.serial_handler.open_serial_port(port, baudrate):
                return jsonify({'success': True, 'message': f'Porta {port} aberta'})
            return jsonify({'success': False, 'message': 'Erro ao abrir porta'})

        @self.app.route('/api/close_serial', methods=['POST'])
        def api_close_serial():
            self.serial_handler.close_serial_port()
            return jsonify({'success': True, 'message': 'Porta serial fechada'})

        @self.app.route('/api/send_serial', methods=['POST'])
        def api_send_serial():
            data = request.json or {}
            payload = data.get('data', '')
            if self.serial_handler.send_serial_data(payload):
                return jsonify({'success': True})
            return jsonify({'success': False, 'message': 'Falha ao enviar dados'})
    
    def setup_socket_events(self):
        @self.socketio.on('connect')
        def handle_connect():
            self.device_service.log_message('Cliente web conectado', "INFO")
            emit('initial_data', {
                'is_scanning': self.device_service.is_scanning,
                'is_scanning_all': self.device_service.is_scanning_all,
                'is_connecting': self.device_service.is_connecting,
                'valid_devices': self.config.valid_devices,
                'detected_devices': {addr: asdict(device) for addr, device in self.device_service.detected_devices.items()},
                'all_devices': {addr: asdict(device) for addr, device in self.device_service.all_devices.items()},
                'device_data': self.device_service.device_data,
                'logs': [asdict(log) for log in self.device_service.logs[-50:]],
                'stats': self.device_service.get_system_stats(),
                'connected_system_devices': self.get_connected_system_devices()
            })
            
        @self.socketio.on('disconnect')
        def handle_disconnect():
            self.device_service.log_message('Cliente web desconectado', "INFO")
    
    def get_connected_system_devices(self):
        connected_devices = []
        try:
            if platform.system() == "Windows":
                result = subprocess.run([
                    'powershell', '-Command',
                    'Get-PnpDevice -Class Bluetooth | Where-Object {$_.Status -eq "OK"} | Select-Object Name, InstanceId'
                ], capture_output=True, text=True, timeout=10)
                
                if result.returncode == 0:
                    lines = result.stdout.strip().split('\n')[3:]
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
                                
            elif platform.system() == "Darwin":
                result = subprocess.run(['system_profiler', 'SPBluetoothDataType'], 
                                      capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    lines = result.stdout.split('\n')
                    for line in lines:
                        if 'Connected: Yes' in line:
                            connected_devices.append({
                                'name': 'Connected Bluetooth Device',
                                'status': 'Connected to System'
                            })
                            
        except subprocess.TimeoutExpired:
            self.device_service.log_message("Timeout ao buscar dispositivos conectados do sistema", "WARNING")
        except Exception as e:
            self.device_service.log_message(f"Erro ao obter dispositivos conectados do sistema: {e}", "ERROR")
            
        return connected_devices
    
    def start_scanning(self):
        if not self.device_service.is_scanning:
            self.device_service.is_scanning = True
            self.device_service.log_message("🚀 Escaneamento de dispositivos autorizados iniciado")
            self._run_scan_thread(authorized_only=True)
            
    def start_scanning_all(self):
        if not self.device_service.is_scanning_all:
            self.device_service.is_scanning_all = True
            self.device_service.log_message("🚀 Escaneamento completo iniciado")
            self._run_scan_thread(authorized_only=False)
            
    def _run_scan_thread(self, authorized_only=True):
        def run_scan():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.device_service.scan_devices(authorized_only))
            except Exception as e:
                self.device_service.log_message(f"Erro no loop de escaneamento: {e}", "ERROR")
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
                self.device_service.log_message(f"Erro ao emitir status de escaneamento: {e}", "ERROR")
            
    def stop_scanning(self):
        self.device_service.is_scanning = False
        self.device_service.is_scanning_all = False
        self.device_service.log_message("⏹️ Escaneamento parado")
        
        if self.socketio:
            try:
                self.socketio.emit('scanning_status', {'is_scanning': False})
                self.socketio.emit('scanning_all_status', {'is_scanning': False})
            except Exception as e:
                self.device_service.log_message(f"Erro ao emitir status de escaneamento: {e}", "ERROR")
    
    def connect_single_device(self, device_address):
        def connect():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.device_service.connect_device(device_address))
            finally:
                loop.close()
                
        thread = threading.Thread(target=connect)
        thread.daemon = True
        thread.start()
        return True
    
    def connect_all_devices(self):
        def connect_all():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.device_service.connect_all_devices())
            finally:
                loop.close()
                
        thread = threading.Thread(target=connect_all)
        thread.daemon = True
        thread.start()
    
    def run(self, host='0.0.0.0', port=None, debug=False):
        port = port or self.config.get('web_port')
        self.socketio.run(self.app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)