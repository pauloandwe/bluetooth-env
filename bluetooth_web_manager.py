import asyncio
import threading
import json
import socket
import time
import requests
from datetime import datetime, timedelta
from bleak import BleakScanner, BleakClient
import logging
from flask import Flask, render_template_string, jsonify, request
from flask_socketio import SocketIO, emit
import signal
import sys
import os
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional
import random

# Configuração de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class DeviceInfo:
    address: str
    name: str
    device_type: str
    last_seen: str
    connected: bool = False
    rssi: Optional[int] = None
    connection_attempts: int = 0
    last_data: Optional[Dict] = None

@dataclass
class LogEntry:
    timestamp: str
    level: str
    message: str
    device_address: Optional[str] = None

class BluetoothWebManager:
    def __init__(self):
        self.connected_devices: Dict[str, DeviceInfo] = {}
        self.device_data: Dict[str, Dict] = {}
        self.is_scanning = False
        self.is_connecting = False
        self.socket_server = None
        self.clients = []
        self.logs: List[LogEntry] = []
        self.scan_task = None
        self.data_update_task = None
        self.socketio = None  # Inicializar como None
        
        # Configurações padrão
        self.config = {
            'socket_port': 8888,
            'web_port': 5001,
            'web_endpoint': 'http://localhost:5001/api/bluetooth-data',
            'scan_interval': 5,
            'data_update_interval': 2,
            'connection_timeout': 10,
            'max_connection_attempts': 3,
            'auto_reconnect': True,
            'known_devices': {
                'bastao': ['Bastão', 'Stick', 'Rod', 'RFID', 'bastao'],
                'balanca': ['Balança', 'Scale', 'Weight', 'Peso', 'balanca'],
                'termometro': ['Temp', 'Temperature', 'Termometro', 'Termo']
            }
        }
        
        # Primeiro configurar Flask e socketio, depois carregar configurações
        self.setup_flask()
        self.load_config()
        
    def load_config(self):
        try:
            with open('bluetooth_config.json', 'r') as f:
                loaded_config = json.load(f)
                self.config.update(loaded_config)
                self.log_message("Configurações carregadas com sucesso", "INFO")
        except FileNotFoundError:
            self.save_config()
            self.log_message("Arquivo de configuração criado", "INFO")
        except Exception as e:
            self.log_message(f"Erro ao carregar configurações: {e}", "ERROR")
            
    def save_config(self):
        try:
            with open('bluetooth_config.json', 'w') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.log_message(f"Erro ao salvar configurações: {e}", "ERROR")
            
    def setup_flask(self):
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = 'bluetooth_animal_management_2024'
        self.socketio = SocketIO(self.app, cors_allowed_origins="*", async_mode='threading')
        
        # Rotas da interface web
        @self.app.route('/')
        def index():
            return render_template_string(WEB_INTERFACE_TEMPLATE)
            
        @self.app.route('/api/status')
        def api_status():
            return jsonify({
                'is_scanning': self.is_scanning,
                'is_connecting': self.is_connecting,
                'connected_devices': {addr: asdict(device) for addr, device in self.connected_devices.items()},
                'device_data': self.device_data,
                'logs': [asdict(log) for log in self.logs[-100:]],
                'stats': self.get_system_stats()
            })
            
        @self.app.route('/api/start_scan', methods=['POST'])
        def api_start_scan():
            if not self.is_scanning:
                self.start_scanning()
                return jsonify({'success': True, 'message': 'Escaneamento iniciado'})
            return jsonify({'success': False, 'message': 'Já está escaneando'})
            
        @self.app.route('/api/stop_scan', methods=['POST'])
        def api_stop_scan():
            if self.is_scanning:
                self.stop_scanning()
                return jsonify({'success': True, 'message': 'Escaneamento parado'})
            return jsonify({'success': False, 'message': 'Não está escaneando'})
            
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
            return jsonify({'success': True, 'message': 'Conectando todos os dispositivos'})
            
        @self.app.route('/api/disconnect_all', methods=['POST'])
        def api_disconnect_all():
            self.disconnect_all_devices()
            return jsonify({'success': True, 'message': 'Todos os dispositivos desconectados'})
            
        @self.app.route('/api/clear_logs', methods=['POST'])
        def api_clear_logs():
            self.logs.clear()
            self.log_message("Logs limpos", "INFO")
            return jsonify({'success': True, 'message': 'Logs limpos'})
            
        @self.app.route('/api/config', methods=['GET', 'POST'])
        def api_config():
            if request.method == 'POST':
                data = request.json
                self.config.update(data)
                self.save_config()
                self.log_message("Configurações atualizadas", "INFO")
                return jsonify({'success': True, 'message': 'Configurações salvas'})
            return jsonify(self.config)
            
        # Socket events
        @self.socketio.on('connect')
        def handle_connect():
            self.log_message('Cliente web conectado', "INFO")
            emit('initial_data', {
                'is_scanning': self.is_scanning,
                'is_connecting': self.is_connecting,
                'connected_devices': {addr: asdict(device) for addr, device in self.connected_devices.items()},
                'device_data': self.device_data,
                'logs': [asdict(log) for log in self.logs[-50:]],
                'stats': self.get_system_stats()
            })
            
        @self.socketio.on('disconnect')
        def handle_disconnect():
            self.log_message('Cliente web desconectado', "INFO")
            
        @self.socketio.on('request_device_data')
        def handle_request_data(data):
            device_address = data.get('device_address')
            if device_address in self.connected_devices:
                self.request_device_data(device_address)
                
    def log_message(self, message, level="INFO", device_address=None):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = LogEntry(
            timestamp=timestamp,
            level=level,
            message=message,
            device_address=device_address
        )
        
        self.logs.append(log_entry)
        
        # Manter apenas os últimos 500 logs
        if len(self.logs) > 500:
            self.logs = self.logs[-500:]
            
        # Log no console
        if level == "ERROR":
            logger.error(message)
        elif level == "WARNING":
            logger.warning(message)
        else:
            logger.info(message)
        
        # Enviar para interface web em tempo real (só se socketio estiver disponível)
        if self.socketio:
            try:
                self.socketio.emit('log_update', asdict(log_entry))
            except Exception as e:
                logger.error(f"Erro ao enviar log via socketio: {e}")
        
    def get_system_stats(self):
        now = datetime.now()
        connected_count = sum(1 for device in self.connected_devices.values() if device.connected)
        recent_logs = len([log for log in self.logs if 
                          datetime.strptime(log.timestamp, "%H:%M:%S").replace(
                              year=now.year, month=now.month, day=now.day
                          ) > now - timedelta(minutes=5)])
        
        return {
            'total_devices': len(self.connected_devices),
            'connected_devices': connected_count,
            'recent_logs': recent_logs,
            'uptime': str(now - getattr(self, 'start_time', now)),
            'data_points': len(self.device_data)
        }
        
    def start_socket_server(self):
        def run_server():
            try:
                self.socket_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.socket_server.bind(('localhost', self.config['socket_port']))
                self.socket_server.listen(5)
                self.log_message(f"Servidor socket iniciado na porta {self.config['socket_port']}")
                
                while True:
                    try:
                        client_socket, addr = self.socket_server.accept()
                        self.clients.append(client_socket)
                        self.log_message(f"Cliente socket conectado: {addr}")
                        
                        client_thread = threading.Thread(target=self.handle_socket_client, args=(client_socket,))
                        client_thread.daemon = True
                        client_thread.start()
                        
                    except Exception as e:
                        if self.socket_server:
                            self.log_message(f"Erro no servidor socket: {e}", "ERROR")
                        break
                        
            except Exception as e:
                self.log_message(f"Erro ao iniciar servidor socket: {e}", "ERROR")
                
        server_thread = threading.Thread(target=run_server)
        server_thread.daemon = True
        server_thread.start()
        
    def handle_socket_client(self, client_socket):
        try:
            while True:
                data = {
                    'timestamp': datetime.now().isoformat(),
                    'devices': self.device_data,
                    'connected_devices': {addr: asdict(device) for addr, device in self.connected_devices.items()},
                    'stats': self.get_system_stats()
                }
                message = json.dumps(data, ensure_ascii=False) + '\n'
                client_socket.send(message.encode('utf-8'))
                time.sleep(self.config['data_update_interval'])
        except Exception as e:
            self.log_message(f"Erro com cliente socket: {e}", "WARNING")
        finally:
            if client_socket in self.clients:
                self.clients.remove(client_socket)
            try:
                client_socket.close()
            except:
                pass
            
    def broadcast_data(self, data):
        # Enviar via socket
        message = json.dumps(data, ensure_ascii=False) + '\n'
        disconnected_clients = []
        
        for client in self.clients:
            try:
                client.send(message.encode('utf-8'))
            except:
                disconnected_clients.append(client)
                
        for client in disconnected_clients:
            self.clients.remove(client)
            
        # Enviar via web socket (só se socketio estiver disponível)
        if self.socketio:
            try:
                self.socketio.emit('device_data_update', data)
            except Exception as e:
                logger.error(f"Erro ao enviar dados via socketio: {e}")
        
    def check_internet_connection(self):
        try:
            requests.get("http://www.google.com", timeout=3)
            return True
        except:
            return False
            
    def send_data_to_web(self, data):
        if self.check_internet_connection():
            try:
                response = requests.post(self.config['web_endpoint'], json=data, timeout=10)
                if response.status_code == 200:
                    self.log_message("Dados enviados para o site com sucesso")
                    return True
                else:
                    self.log_message(f"Erro ao enviar dados para o site: {response.status_code}", "WARNING")
            except Exception as e:
                self.log_message(f"Erro ao conectar com o site: {e}", "WARNING")
        
        # Sempre enviar localmente
        self.broadcast_data(data)
        return False
        
    def get_device_type(self, device_name):
        if not device_name:
            return 'desconhecido'
            
        device_name_lower = device_name.lower()
        
        for device_type, keywords in self.config['known_devices'].items():
            for keyword in keywords:
                if keyword.lower() in device_name_lower:
                    return device_type
                    
        return 'desconhecido'
        
    async def scan_devices(self):
        while self.is_scanning:
            try:
                self.log_message("🔍 Escaneando dispositivos Bluetooth...")
                
                # Scanner com timeout menor para mais responsividade
                devices = await BleakScanner.discover(timeout=8.0, return_adv=True)
                
                current_time = datetime.now()
                found_devices = []
                new_devices = 0
                
                for device_address, (device, adv_data) in devices.items():
                    if device.name:
                        device_type = self.get_device_type(device.name)
                        
                        is_new_device = device_address not in self.connected_devices
                        
                        device_info = DeviceInfo(
                            address=device_address,
                            name=device.name,
                            device_type=device_type,
                            last_seen=current_time.isoformat(),
                            connected=False,
                            rssi=getattr(adv_data, 'rssi', None)
                        )
                        
                        found_devices.append(device_info)
                        
                        if is_new_device:
                            self.connected_devices[device_address] = device_info
                            new_devices += 1
                            self.log_message(f"📱 Novo dispositivo: {device.name} ({device_type}) - RSSI: {device_info.rssi}")
                        else:
                            # Atualizar informações do dispositivo existente
                            existing_device = self.connected_devices[device_address]
                            existing_device.last_seen = current_time.isoformat()
                            existing_device.rssi = device_info.rssi
                
                if new_devices > 0:
                    self.log_message(f"✨ {new_devices} novos dispositivos encontrados")
                
                # Enviar atualizações para interface web
                if self.socketio:
                    try:
                        self.socketio.emit('devices_update', {
                            'connected_devices': {addr: asdict(device) for addr, device in self.connected_devices.items()},
                            'found_devices': [asdict(device) for device in found_devices],
                            'stats': self.get_system_stats()
                        })
                    except Exception as e:
                        logger.error(f"Erro ao enviar atualização de dispositivos: {e}")
                            
                await asyncio.sleep(self.config['scan_interval'])
                
            except Exception as e:
                self.log_message(f"❌ Erro durante escaneamento: {e}", "ERROR")
                await asyncio.sleep(3)
                
    def start_scanning(self):
        if not self.is_scanning:
            self.is_scanning = True
            self.log_message("🚀 Escaneamento Bluetooth iniciado")
            
            def run_scan():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(self.scan_devices())
                except Exception as e:
                    self.log_message(f"Erro no loop de escaneamento: {e}", "ERROR")
                finally:
                    loop.close()
                
            scan_thread = threading.Thread(target=run_scan)
            scan_thread.daemon = True
            scan_thread.start()
            
            # Iniciar atualização automática de dados
            self.start_data_updates()
            
            if self.socketio:
                try:
                    self.socketio.emit('scanning_status', {'is_scanning': True})
                except Exception as e:
                    logger.error(f"Erro ao emitir status de escaneamento: {e}")
            
    def stop_scanning(self):
        self.is_scanning = False
        self.log_message("⏹️ Escaneamento parado")
        if self.socketio:
            try:
                self.socketio.emit('scanning_status', {'is_scanning': False})
            except Exception as e:
                logger.error(f"Erro ao emitir status de escaneamento: {e}")
        
    def start_data_updates(self):
        """Inicia thread para atualização automática de dados dos dispositivos"""
        def update_loop():
            while self.is_scanning:
                try:
                    for device_address, device in self.connected_devices.items():
                        if device.connected:
                            # Simular atualização de dados
                            self.simulate_device_data(device_address)
                    
                    time.sleep(self.config['data_update_interval'])
                except Exception as e:
                    self.log_message(f"Erro na atualização de dados: {e}", "ERROR")
                    time.sleep(5)
        
        update_thread = threading.Thread(target=update_loop)
        update_thread.daemon = True
        update_thread.start()
        
    def simulate_device_data(self, device_address):
        """Simula dados dos dispositivos para demonstração"""
        device_info = self.connected_devices.get(device_address)
        if not device_info:
            return
            
        try:
            current_time = datetime.now()
            
            if device_info.device_type == 'balanca':
                # Simular variação realista de peso
                base_weight = 450
                variation = random.uniform(-10, 10)
                weight = round(base_weight + variation, 1)
                processed_data = {
                    'peso': weight,
                    'unidade': 'kg',
                    'estabilidade': random.choice(['estavel', 'oscilando']),
                    'bateria': random.randint(60, 100)
                }
            elif device_info.device_type == 'bastao':
                # Simular leitura de RFID
                if random.random() < 0.3:  # 30% chance de ler um novo animal
                    animal_ids = ['RF001', 'RF002', 'RF003', 'RF004', 'RF005']
                    animal_id = random.choice(animal_ids)
                    processed_data = {
                        'animal_id': animal_id,
                        'tipo': 'identificacao',
                        'signal_strength': random.randint(70, 100),
                        'bateria': random.randint(80, 100)
                    }
                else:
                    return  # Não há nova leitura
            elif device_info.device_type == 'termometro':
                # Simular temperatura corporal
                temp = round(random.uniform(37.5, 39.2), 1)
                processed_data = {
                    'temperatura': temp,
                    'unidade': '°C',
                    'status': 'normal' if 37.8 <= temp <= 38.8 else 'alerta',
                    'bateria': random.randint(50, 100)
                }
            else:
                processed_data = {
                    'status': 'conectado',
                    'timestamp': current_time.isoformat(),
                    'bateria': random.randint(20, 100)
                }
                
            if processed_data:
                device_data = {
                    'device_address': device_address,
                    'name': device_info.name,
                    'type': device_info.device_type,
                    'data': processed_data,
                    'timestamp': current_time.isoformat(),
                    'rssi': device_info.rssi
                }
                
                self.device_data[device_address] = device_data
                device_info.last_data = processed_data
                
                # Log específico para dados importantes
                if device_info.device_type == 'balanca':
                    self.log_message(f"⚖️ Peso registrado: {processed_data['peso']} kg", "INFO", device_address)
                elif device_info.device_type == 'bastao' and 'animal_id' in processed_data:
                    self.log_message(f"🐮 Animal identificado: {processed_data['animal_id']}", "INFO", device_address)
                elif device_info.device_type == 'termometro':
                    status_emoji = "🌡️" if processed_data['status'] == 'normal' else "🚨"
                    self.log_message(f"{status_emoji} Temperatura: {processed_data['temperatura']}°C", 
                                   "WARNING" if processed_data['status'] == 'alerta' else "INFO", device_address)
                
                self.send_data_to_web(device_data)
                
        except Exception as e:
            self.log_message(f"Erro ao simular dados do dispositivo {device_address}: {e}", "ERROR")
            
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
        if device_address in self.connected_devices:
            self.connected_devices[device_address].connected = False
            self.connected_devices[device_address].connection_attempts = 0
            if device_address in self.device_data:
                del self.device_data[device_address]
            self.log_message(f"🔌 Dispositivo {self.connected_devices[device_address].name} desconectado", "INFO")
            if self.socketio:
                try:
                    self.socketio.emit('device_disconnected', {'device_address': device_address})
                except Exception as e:
                    logger.error(f"Erro ao emitir desconexão de dispositivo: {e}")
            
    async def connect_device(self, device_address):
        try:
            device_info = self.connected_devices.get(device_address)
            if not device_info:
                return False
                
            if device_info.connection_attempts >= self.config['max_connection_attempts']:
                self.log_message(f"❌ Máximo de tentativas de conexão atingido para {device_info.name}", "WARNING")
                return False
                
            device_info.connection_attempts += 1
            self.log_message(f"🔗 Tentativa {device_info.connection_attempts}: Conectando ao {device_info.name}")
            
            self.is_connecting = True
            if self.socketio:
                try:
                    self.socketio.emit('connection_status', {'is_connecting': True, 'device_address': device_address})
                except Exception as e:
                    logger.error(f"Erro ao emitir status de conexão: {e}")
            
            try:
                async with BleakClient(device_address, timeout=self.config['connection_timeout']) as client:
                    if client.is_connected:
                        device_info.connected = True
                        device_info.connection_attempts = 0
                        self.log_message(f"✅ Conectado ao dispositivo: {device_info.name}")
                        
                        # Simular leitura inicial de dados
                        await asyncio.sleep(1)
                        self.simulate_device_data(device_address)
                        
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
                self.log_message(f"⏰ Timeout ao conectar com {device_info.name}", "WARNING")
            except Exception as e:
                self.log_message(f"❌ Erro ao conectar com {device_info.name}: {e}", "ERROR")
                
        except Exception as e:
            self.log_message(f"❌ Erro geral na conexão com {device_address}: {e}", "ERROR")
        finally:
            self.is_connecting = False
            if self.socketio:
                try:
                    self.socketio.emit('connection_status', {'is_connecting': False, 'device_address': device_address})
                except Exception as e:
                    logger.error(f"Erro ao emitir status de conexão: {e}")
            
        return False
        
    def connect_all_devices(self):
        def connect_all():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            tasks = []
            disconnected_devices = [addr for addr, device in self.connected_devices.items() 
                                  if not device.connected]
            
            if not disconnected_devices:
                self.log_message("ℹ️ Todos os dispositivos já estão conectados")
                return
                
            self.log_message(f"🔗 Conectando {len(disconnected_devices)} dispositivos...")
            
            for device_address in disconnected_devices:
                task = self.connect_device(device_address)
                tasks.append(task)
                    
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
        disconnected_count = 0
        for device_address, device in self.connected_devices.items():
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
        
    def request_device_data(self, device_address):
        """Força atualização de dados de um dispositivo específico"""
        if device_address in self.connected_devices and self.connected_devices[device_address].connected:
            self.simulate_device_data(device_address)
            self.log_message(f"📊 Dados atualizados para {self.connected_devices[device_address].name}")
        
    def run(self):
        self.start_time = datetime.now()
        self.log_message("🚀 Sistema de monitoramento Bluetooth iniciado")
        self.start_socket_server()
        
        # Handler para shutdown graceful
        def signal_handler(sig, frame):
            self.log_message("🛑 Encerrando sistema...")
            self.is_scanning = False
            if self.socket_server:
                self.socket_server.close()
            for client in self.clients:
                try:
                    client.close()
                except:
                    pass
            sys.exit(0)
            
        signal.signal(signal.SIGINT, signal_handler)
        
        print(f"\n🎉 Sistema Bluetooth para Manejo Animal iniciado com sucesso!")
        print(f"📱 Interface Web: http://localhost:{self.config['web_port']}")
        print(f"🔌 Socket Server: localhost:{self.config['socket_port']}")
        print(f"📋 Logs em tempo real disponíveis na interface web")
        print(f"📊 Atualização automática de dados a cada {self.config['data_update_interval']}s")
        print(f"⚠️  Use Ctrl+C para parar o sistema\n")
        
        # Iniciar servidor web
        self.socketio.run(self.app, 
                         host='0.0.0.0', 
                         port=self.config['web_port'], 
                         debug=False,
                         allow_unsafe_werkzeug=True)

# Template da interface web atualizada
WEB_INTERFACE_TEMPLATE = '''
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sistema Bluetooth - Manejo Animal</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.0/socket.io.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: 'Segoe UI', sans-serif; 
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
            min-height: 100vh; 
            padding: 20px; 
        }
        .container { max-width: 1400px; margin: 0 auto; }
        .header { 
            background: rgba(255,255,255,0.95); 
            border-radius: 15px; 
            padding: 20px; 
            margin-bottom: 20px; 
            text-align: center;
            box-shadow: 0 8px 32px rgba(0,0,0,0.1);
        }
        .status-bar { 
            display: flex; 
            justify-content: space-between; 
            flex-wrap: wrap;
            background: #f8f9fa; 
            padding: 15px 20px; 
            border-radius: 10px; 
            margin-top: 15px; 
            gap: 10px;
        }
        .status-item {
            display: flex;
            flex-direction: column;
            align-items: center;
            min-width: 120px;
        }
        .status-label { font-size: 0.8em; color: #666; }
        .status-value { font-weight: bold; font-size: 1.1em; }
        .card { 
            background: rgba(255,255,255,0.95); 
            border-radius: 15px; 
            padding: 25px; 
            margin-bottom: 20px; 
            box-shadow: 0 8px 32px rgba(0,0,0,0.1);
        }
        .controls { 
            text-align: center; 
            margin: 20px 0; 
            display: flex;
            justify-content: center;
            flex-wrap: wrap;
            gap: 10px;
        }
        .btn { 
            background: linear-gradient(45deg, #667eea, #764ba2); 
            color: white; 
            border: none; 
            padding: 12px 25px; 
            border-radius: 25px; 
            cursor: pointer; 
            font-size: 14px;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .btn:hover { 
            opacity: 0.9; 
            transform: translateY(-2px);
            box-shadow: 0 4px 15px rgba(0,0,0,0.2);
        }
        .btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
            transform: none;
        }
        .btn-danger { background: linear-gradient(45deg, #ff6b6b, #ee5a24); }
        .btn-success { background: linear-gradient(45deg, #26de81, #20bf6b); }
        .btn-warning { background: linear-gradient(45deg, #fed330, #f7931e); }
        .devices-grid { 
            display: grid; 
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); 
            gap: 20px; 
        }
        .device-card { 
            background: #f8f9fa; 
            padding: 20px; 
            border-radius: 12px; 
            transition: all 0.3s ease;
            border-left: 4px solid #dc3545;
        }
        .device-card:hover {
            transform: translateY(-3px);
            box-shadow: 0 6px 20px rgba(0,0,0,0.1);
        }
        .device-connected { border-left-color: #28a745; }
        .device-card h4 {
            margin-bottom: 10px;
            color: #333;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .device-info {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
            margin: 10px 0;
            font-size: 0.9em;
        }
        .device-actions {
            margin-top: 15px;
            display: flex;
            gap: 10px;
        }
        .device-actions .btn {
            flex: 1;
            padding: 8px 15px;
            font-size: 12px;
        }
        .logs { 
            background: #1a1a1a; 
            color: #e0e0e0; 
            padding: 20px; 
            border-radius: 12px; 
            height: 400px; 
            overflow-y: auto; 
            font-family: 'Consolas', monospace; 
            font-size: 13px;
            line-height: 1.4;
        }
        .log-entry { 
            margin-bottom: 8px; 
            padding: 5px 10px;
            border-radius: 4px;
            transition: background-color 0.3s ease;
        }
        .log-entry.new-log {
            background-color: rgba(102, 126, 234, 0.2);
            animation: highlight 1s ease-out;
        }
        @keyframes highlight {
            0% { background-color: rgba(102, 126, 234, 0.4); }
            100% { background-color: transparent; }
        }
        .log-timestamp { color: #7f8c8d; }
        .log-level-INFO { color: #3498db; }
        .log-level-WARNING { color: #f39c12; }
        .log-level-ERROR { color: #e74c3c; }
        .status-connected { color: #28a745; font-weight: bold; }
        .status-disconnected { color: #dc3545; font-weight: bold; }
        .status-connecting { color: #ffc107; font-weight: bold; }
        .data-display { 
            background: linear-gradient(135deg, #e3f2fd 0%, #f3e5f5 100%); 
            padding: 20px; 
            border-radius: 12px; 
            margin: 15px 0; 
            border: 1px solid #e0e0e0;
        }
        .weight-display { 
            font-size: 2.5em; 
            color: #1976d2; 
            text-align: center; 
            font-weight: bold;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.1);
        }
        .animal-display {
            font-size: 1.8em;
            color: #f57c00;
            text-align: center;
            font-weight: bold;
        }
        .temperature-display {
            font-size: 2em;
            text-align: center;
            font-weight: bold;
        }
        .temp-normal { color: #4caf50; }
        .temp-alert { color: #f44336; }
        .battery-indicator {
            display: inline-block;
            width: 60px;
            height: 20px;
            border: 2px solid #ccc;
            border-radius: 3px;
            position: relative;
            margin-left: 5px;
        }
        .battery-level {
            height: 100%;
            border-radius: 1px;
            transition: all 0.3s ease;
        }
        .battery-high { background: #4caf50; }
        .battery-medium { background: #ff9800; }
        .battery-low { background: #f44336; }
        .rssi-indicator {
            display: inline-flex;
            align-items: center;
            gap: 5px;
        }
        .signal-bars {
            display: flex;
            align-items: end;
            gap: 2px;
        }
        .signal-bar {
            width: 3px;
            background: #ccc;
            border-radius: 1px;
        }
        .signal-bar.active { background: #4caf50; }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-top: 15px;
        }
        .stat-item {
            text-align: center;
            padding: 15px;
            background: #f8f9fa;
            border-radius: 8px;
        }
        .stat-value {
            font-size: 1.5em;
            font-weight: bold;
            color: #667eea;
        }
        .stat-label {
            font-size: 0.9em;
            color: #666;
            margin-top: 5px;
        }
        .auto-update {
            position: fixed;
            top: 20px;
            right: 20px;
            background: rgba(0,0,0,0.8);
            color: white;
            padding: 10px 15px;
            border-radius: 20px;
            font-size: 12px;
            z-index: 1000;
        }
        .pulse {
            animation: pulse 1.5s infinite;
        }
        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.5; }
            100% { opacity: 1; }
        }
    </style>
</head>
<body>
    <div class="auto-update" id="auto-update">
        🟢 Atualização automática ativa
    </div>
    
    <div class="container">
        <div class="header">
            <h1>🐄 Sistema Bluetooth - Manejo Animal</h1>
            <div class="status-bar">
                <div class="status-item">
                    <div class="status-label">Status Scan</div>
                    <div id="scan-status" class="status-value status-disconnected">Parado</div>
                </div>
                <div class="status-item">
                    <div class="status-label">Dispositivos</div>
                    <div id="device-count" class="status-value">0</div>
                </div>
                <div class="status-item">
                    <div class="status-label">Conectados</div>
                    <div id="connected-count" class="status-value">0</div>
                </div>
                <div class="status-item">
                    <div class="status-label">Última Atualização</div>
                    <div id="last-update" class="status-value">--:--:--</div>
                </div>
                <div class="status-item">
                    <div class="status-label">Uptime</div>
                    <div id="uptime" class="status-value">--</div>
                </div>
            </div>
        </div>
        
        <div class="controls">
            <button class="btn" onclick="startScan()" id="start-btn">
                🔍 Iniciar Escaneamento
            </button>
            <button class="btn btn-danger" onclick="stopScan()" id="stop-btn">
                ⏹️ Parar Escaneamento
            </button>
            <button class="btn btn-success" onclick="connectAll()" id="connect-all-btn">
                🔗 Conectar Todos
            </button>
            <button class="btn btn-warning" onclick="disconnectAll()" id="disconnect-all-btn">
                🔌 Desconectar Todos
            </button>
            <button class="btn" onclick="clearLogs()">
                🗑️ Limpar Logs
            </button>
        </div>
        
        <div class="card">
            <h2>📱 Dispositivos Detectados</h2>
            <div id="devices-container" class="devices-grid">
                <div class="device-card">Nenhum dispositivo detectado ainda...</div>
            </div>
        </div>
        
        <div class="card">
            <h2>📊 Dados dos Dispositivos</h2>
            <div id="data-container">
                <p>Aguardando dados dos dispositivos...</p>
            </div>
        </div>
        
        <div class="card">
            <h2>📈 Estatísticas do Sistema</h2>
            <div id="stats-container" class="stats-grid">
                <div class="stat-item">
                    <div class="stat-value" id="stat-total">0</div>
                    <div class="stat-label">Total Dispositivos</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value" id="stat-connected">0</div>
                    <div class="stat-label">Conectados</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value" id="stat-data">0</div>
                    <div class="stat-label">Pontos de Dados</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value" id="stat-logs">0</div>
                    <div class="stat-label">Logs Recentes</div>
                </div>
            </div>
        </div>
        
        <div class="card">
            <h2>📋 Logs do Sistema em Tempo Real</h2>
            <div id="logs-container" class="logs"></div>
        </div>
    </div>

    <script>
        const socket = io();
        let isScanning = false;
        let isConnecting = false;
        
        socket.on('connect', function() {
            console.log('Conectado ao servidor');
            updateAutoUpdateStatus(true);
            updateLastUpdate();
        });
        
        socket.on('disconnect', function() {
            console.log('Desconectado do servidor');
            updateAutoUpdateStatus(false);
        });
        
        socket.on('initial_data', function(data) {
            console.log('Dados iniciais recebidos:', data);
            updateScanStatus(data.is_scanning);
            updateConnectingStatus(data.is_connecting);
            updateDevicesDisplay(data.connected_devices);
            updateDataDisplay(data.device_data);
            updateStats(data.stats);
            data.logs.forEach(log => addLogEntry(log, false));
            updateLastUpdate();
        });
        
        socket.on('devices_update', function(data) {
            updateDevicesDisplay(data.connected_devices);
            if (data.stats) updateStats(data.stats);
            updateLastUpdate();
        });
        
        socket.on('device_data_update', function(data) {
            updateSingleDeviceData(data);
            updateLastUpdate();
        });
        
        socket.on('log_update', function(log) {
            addLogEntry(log, true);
        });
        
        socket.on('scanning_status', function(data) {
            updateScanStatus(data.is_scanning);
        });
        
        socket.on('connection_status', function(data) {
            updateConnectingStatus(data.is_connecting);
            if (data.device_address) {
                updateDeviceConnectingStatus(data.device_address, data.is_connecting);
            }
        });
        
        socket.on('device_connected', function(data) {
            addLogEntry({
                timestamp: new Date().toLocaleTimeString(),
                level: 'INFO',
                message: `✅ ${data.device_name} conectado com sucesso`
            }, true);
        });
        
        socket.on('device_disconnected', function(data) {
            removeDeviceData(data.device_address);
        });
        
        socket.on('all_devices_disconnected', function(data) {
            addLogEntry({
                timestamp: new Date().toLocaleTimeString(),
                level: 'INFO',
                message: `🔌 ${data.disconnected_count} dispositivos desconectados`
            }, true);
        });
        
        function updateAutoUpdateStatus(connected) {
            const indicator = document.getElementById('auto-update');
            if (connected) {
                indicator.innerHTML = '🟢 Atualização automática ativa';
                indicator.classList.remove('pulse');
            } else {
                indicator.innerHTML = '🔴 Desconectado';
                indicator.classList.add('pulse');
            }
        }
        
        function updateScanStatus(scanning) {
            isScanning = scanning;
            const statusEl = document.getElementById('scan-status');
            const startBtn = document.getElementById('start-btn');
            const stopBtn = document.getElementById('stop-btn');
            
            if (scanning) {
                statusEl.textContent = 'Escaneando';
                statusEl.className = 'status-value status-connected pulse';
                startBtn.disabled = true;
                stopBtn.disabled = false;
            } else {
                statusEl.textContent = 'Parado';
                statusEl.className = 'status-value status-disconnected';
                startBtn.disabled = false;
                stopBtn.disabled = true;
            }
        }
        
        function updateConnectingStatus(connecting) {
            isConnecting = connecting;
            const connectAllBtn = document.getElementById('connect-all-btn');
            
            if (connecting) {
                connectAllBtn.innerHTML = '⏳ Conectando...';
                connectAllBtn.disabled = true;
            } else {
                connectAllBtn.innerHTML = '🔗 Conectar Todos';
                connectAllBtn.disabled = false;
            }
        }
        
        function updateDeviceConnectingStatus(deviceAddress, connecting) {
            const deviceCard = document.querySelector(`[data-device="${deviceAddress}"]`);
            if (deviceCard) {
                const connectBtn = deviceCard.querySelector('.btn-connect');
                if (connectBtn) {
                    if (connecting) {
                        connectBtn.innerHTML = '⏳ Conectando...';
                        connectBtn.disabled = true;
                    } else {
                        connectBtn.innerHTML = '🔗 Conectar';
                        connectBtn.disabled = false;
                    }
                }
            }
        }
        
        function getBatteryHTML(level) {
            if (!level) return '';
            const batteryClass = level > 60 ? 'battery-high' : level > 30 ? 'battery-medium' : 'battery-low';
            return `
                <div class="battery-indicator">
                    <div class="battery-level ${batteryClass}" style="width: ${level}%"></div>
                </div>
                <span>${level}%</span>
            `;
        }
        
        function getRSSIHTML(rssi) {
            if (!rssi) return '<span>-- dBm</span>';
            
            const strength = Math.min(4, Math.max(0, Math.floor((rssi + 100) / 15)));
            let bars = '';
            for (let i = 1; i <= 4; i++) {
                const height = i * 3 + 2;
                const active = i <= strength ? 'active' : '';
                bars += `<div class="signal-bar ${active}" style="height: ${height}px"></div>`;
            }
            
            return `
                <div class="rssi-indicator">
                    <div class="signal-bars">${bars}</div>
                    <span>${rssi} dBm</span>
                </div>
            `;
        }
        
        function updateDevicesDisplay(devices) {
            const container = document.getElementById('devices-container');
            const deviceCount = document.getElementById('device-count');
            const connectedCount = document.getElementById('connected-count');
            
            if (Object.keys(devices).length === 0) {
                container.innerHTML = '<div class="device-card">Nenhum dispositivo detectado ainda...</div>';
                deviceCount.textContent = '0';
                connectedCount.textContent = '0';
                return;
            }
            
            const connected = Object.values(devices).filter(d => d.connected).length;
            deviceCount.textContent = Object.keys(devices).length;
            connectedCount.textContent = connected;
            
            container.innerHTML = Object.values(devices).map(device => {
                const typeEmoji = {
                    'balanca': '⚖️',
                    'bastao': '📡',
                    'termometro': '🌡️',
                    'desconhecido': '❓'
                }[device.device_type] || '📱';
                
                const battery = device.last_data && device.last_data.bateria ? 
                    getBatteryHTML(device.last_data.bateria) : '';
                const rssi = getRSSIHTML(device.rssi);
                
                return `
                    <div class="device-card ${device.connected ? 'device-connected' : 'device-disconnected'}" 
                         data-device="${device.address}">
                        <h4>${typeEmoji} ${device.name}</h4>
                        <div class="device-info">
                            <div><strong>Tipo:</strong> ${device.device_type}</div>
                            <div><strong>Status:</strong> 
                                <span class="${device.connected ? 'status-connected' : 'status-disconnected'}">
                                    ${device.connected ? 'Conectado' : 'Desconectado'}
                                </span>
                            </div>
                            <div><strong>Endereço:</strong> ${device.address}</div>
                            <div><strong>Sinal:</strong> ${rssi}</div>
                            <div><strong>Última visão:</strong> ${new Date(device.last_seen).toLocaleTimeString()}</div>
                            <div><strong>Bateria:</strong> ${battery || 'N/A'}</div>
                        </div>
                        <div class="device-actions">
                            ${!device.connected ? 
                                `<button class="btn btn-success btn-connect" onclick="connectDevice('${device.address}')">🔗 Conectar</button>` :
                                `<button class="btn btn-danger" onclick="disconnectDevice('${device.address}')">🔌 Desconectar</button>`
                            }
                            <button class="btn" onclick="requestDeviceData('${device.address}')">📊 Atualizar</button>
                        </div>
                    </div>
                `;
            }).join('');
        }
        
        function updateDataDisplay(data) {
            const container = document.getElementById('data-container');
            let html = '';
            
            Object.values(data).forEach(device => {
                if (device.data) {
                    html += `<div class="data-display">
                        <h4>${getDeviceEmoji(device.type)} ${device.name} (${device.type})</h4>`;
                    
                    if (device.type === 'balanca' && device.data.peso) {
                        html += `
                            <div class="weight-display">${device.data.peso} ${device.data.unidade}</div>
                            <div style="text-align: center; margin-top: 10px;">
                                <small>Estabilidade: ${device.data.estabilidade}</small>
                            </div>
                        `;
                    } else if (device.type === 'bastao' && device.data.animal_id) {
                        html += `
                            <div class="animal-display">🐮 Animal: ${device.data.animal_id}</div>
                            <div style="text-align: center; margin-top: 10px;">
                                <small>Força do sinal: ${device.data.signal_strength}%</small>
                            </div>
                        `;
                    } else if (device.type === 'termometro' && device.data.temperatura) {
                        const tempClass = device.data.status === 'normal' ? 'temp-normal' : 'temp-alert';
                        html += `
                            <div class="temperature-display ${tempClass}">
                                ${device.data.status === 'normal' ? '🌡️' : '🚨'} 
                                ${device.data.temperatura} ${device.data.unidade}
                            </div>
                            <div style="text-align: center; margin-top: 10px;">
                                <small>Status: ${device.data.status}</small>
                            </div>
                        `;
                    } else {
                        html += `<pre style="background: #f0f0f0; padding: 10px; border-radius: 5px;">${JSON.stringify(device.data, null, 2)}</pre>`;
                    }
                    
                    html += `
                        <div style="text-align: center; margin-top: 15px;">
                            <small>📅 ${new Date(device.timestamp).toLocaleString()}</small>
                            ${device.rssi ? `<br><small>📶 RSSI: ${device.rssi} dBm</small>` : ''}
                        </div>
                    </div>`;
                }
            });
            
            container.innerHTML = html || '<p>Aguardando dados dos dispositivos...</p>';
        }
        
        function updateSingleDeviceData(deviceData) {
            // Atualizar apenas um dispositivo específico
            const existingData = {};
            existingData[deviceData.device_address] = deviceData;
            updateDataDisplay(existingData);
        }
        
        function removeDeviceData(deviceAddress) {
            const dataDisplay = document.querySelector(`[data-device="${deviceAddress}"]`);
            if (dataDisplay) {
                dataDisplay.style.transition = 'opacity 0.3s ease';
                dataDisplay.style.opacity = '0.5';
            }
        }
        
        function updateStats(stats) {
            if (!stats) return;
            
            document.getElementById('stat-total').textContent = stats.total_devices || 0;
            document.getElementById('stat-connected').textContent = stats.connected_devices || 0;
            document.getElementById('stat-data').textContent = stats.data_points || 0;
            document.getElementById('stat-logs').textContent = stats.recent_logs || 0;
            document.getElementById('uptime').textContent = stats.uptime || '--';
        }
        
        function getDeviceEmoji(type) {
            const emojis = {
                'balanca': '⚖️',
                'bastao': '📡',
                'termometro': '🌡️',
                'desconhecido': '❓'
            };
            return emojis[type] || '📱';
        }
        
        function addLogEntry(log, animate = false) {
            const container = document.getElementById('logs-container');
            const entry = document.createElement('div');
            entry.className = `log-entry ${animate ? 'new-log' : ''}`;
            
            const levelClass = `log-level-${log.level}`;
            const deviceInfo = log.device_address ? ` [${log.device_address.substr(-4)}]` : '';
            
            entry.innerHTML = `
                <span class="log-timestamp">[${log.timestamp}]</span> 
                <span class="${levelClass}">[${log.level}]</span>${deviceInfo} 
                ${log.message}
            `;
            
            container.appendChild(entry);
            container.scrollTop = container.scrollHeight;
            
            // Remover classe de animação após a animação
            if (animate) {
                setTimeout(() => entry.classList.remove('new-log'), 1000);
            }
            
            // Limitar número de logs exibidos
            const maxLogs = 200;
            while (container.children.length > maxLogs) {
                container.removeChild(container.firstChild);
            }
        }
        
        function updateLastUpdate() {
            document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
        }
        
        // Funções de controle
        function startScan() {
            fetch('/api/start_scan', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        addLogEntry({
                            timestamp: new Date().toLocaleTimeString(),
                            level: 'INFO',
                            message: data.message
                        }, true);
                    }
                });
        }
        
        function stopScan() {
            fetch('/api/stop_scan', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        addLogEntry({
                            timestamp: new Date().toLocaleTimeString(),
                            level: 'INFO',
                            message: data.message
                        }, true);
                    }
                });
        }
        
        function connectDevice(deviceAddress) {
            fetch(`/api/connect_device/${deviceAddress}`, { method: 'POST' })
                .then(response => response.json())
                .then(data => console.log(data.message));
        }
        
        function disconnectDevice(deviceAddress) {
            fetch(`/api/disconnect_device/${deviceAddress}`, { method: 'POST' })
                .then(response => response.json())
                .then(data => console.log(data.message));
        }
        
        function connectAll() {
            fetch('/api/connect_all', { method: 'POST' })
                .then(response => response.json())
                .then(data => console.log(data.message));
        }
        
        function disconnectAll() {
            fetch('/api/disconnect_all', { method: 'POST' })
                .then(response => response.json())
                .then(data => console.log(data.message));
        }
        
        function clearLogs() {
            fetch('/api/clear_logs', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        document.getElementById('logs-container').innerHTML = '';
                    }
                });
        }
        
        function requestDeviceData(deviceAddress) {
            socket.emit('request_device_data', { device_address: deviceAddress });
        }
        
        // Carregar status inicial
        fetch('/api/status')
            .then(response => response.json())
            .then(data => {
                updateScanStatus(data.is_scanning);
                updateConnectingStatus(data.is_connecting);
                updateDevicesDisplay(data.connected_devices || {});
                updateDataDisplay(data.device_data || {});
                updateStats(data.stats);
                data.logs.forEach(log => addLogEntry(log, false));
                updateLastUpdate();
            })
            .catch(error => {
                console.error('Erro ao carregar dados iniciais:', error);
                addLogEntry({
                    timestamp: new Date().toLocaleTimeString(),
                    level: 'ERROR',
                    message: 'Erro ao conectar com o servidor'
                }, true);
            });
    </script>
</body>
</html>
'''

if __name__ == "__main__":
    app = BluetoothWebManager()
    app.run()