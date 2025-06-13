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
    last_seen: str
    connected: bool = False
    rssi: Optional[int] = None
    connection_attempts: int = 0

@dataclass
class LogEntry:
    timestamp: str
    level: str
    message: str
    device_address: Optional[str] = None

class BluetoothWebManager:
    def __init__(self):
        # ARRAY DE ENDEREÇOS MAC VÁLIDOS - MODIFIQUE AQUI
        self.valid_mac_addresses = [
            "AA:BB:CC:DD:EE:FF",  # Exemplo 1
            "11:22:33:44:55:66",  # Exemplo 2
            "77:88:99:AA:BB:CC",  # Exemplo 3
            "2A328859-8CB4-994A-F780-440D72EF1A0E",
        ]
        
        self.detected_devices: Dict[str, DeviceInfo] = {}  # Apenas dispositivos válidos detectados
        self.device_data: Dict[str, Dict] = {}
        self.is_scanning = False
        self.is_connecting = False
        self.socket_server = None
        self.clients = []
        self.logs: List[LogEntry] = []
        self.socketio = None
        
        # Configurações padrão
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
        
    def load_config(self):
        try:
            with open('bluetooth_config.json', 'r') as f:
                loaded_config = json.load(f)
                self.config.update(loaded_config)
                # Carregar lista de MACs válidos se existir no arquivo
                if 'valid_mac_addresses' in loaded_config:
                    self.valid_mac_addresses = loaded_config['valid_mac_addresses']
                self.log_message("Configurações carregadas com sucesso", "INFO")
        except FileNotFoundError:
            self.save_config()
            self.log_message("Arquivo de configuração criado", "INFO")
        except Exception as e:
            self.log_message(f"Erro ao carregar configurações: {e}", "ERROR")
            
    def save_config(self):
        try:
            # Salvar configurações incluindo a lista de MACs válidos
            config_to_save = self.config.copy()
            config_to_save['valid_mac_addresses'] = self.valid_mac_addresses
            
            with open('bluetooth_config.json', 'w') as f:
                json.dump(config_to_save, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.log_message(f"Erro ao salvar configurações: {e}", "ERROR")
            
    def setup_flask(self):
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = 'bluetooth_whitelist_2024'
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
                'valid_mac_addresses': self.valid_mac_addresses,
                'detected_devices': {addr: asdict(device) for addr, device in self.detected_devices.items()},
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
            if device_address in self.valid_mac_addresses:
                success = self.connect_single_device(device_address)
                if success:
                    return jsonify({'success': True, 'message': f'Conectando dispositivo {device_address}'})
                return jsonify({'success': False, 'message': 'Erro ao conectar dispositivo'})
            return jsonify({'success': False, 'message': 'Dispositivo não autorizado'})
            
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
            if 'mac_addresses' in data and isinstance(data['mac_addresses'], list):
                self.valid_mac_addresses = data['mac_addresses']
                self.save_config()
                self.log_message(f"Lista de MACs válidos atualizada: {len(self.valid_mac_addresses)} endereços", "INFO")
                return jsonify({'success': True, 'message': 'Lista de MACs válidos atualizada'})
            return jsonify({'success': False, 'message': 'Formato inválido'})
            
        # Socket events
        @self.socketio.on('connect')
        def handle_connect():
            self.log_message('Cliente web conectado', "INFO")
            emit('initial_data', {
                'is_scanning': self.is_scanning,
                'is_connecting': self.is_connecting,
                'valid_mac_addresses': self.valid_mac_addresses,
                'detected_devices': {addr: asdict(device) for addr, device in self.detected_devices.items()},
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
            if device_address in self.detected_devices:
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
        
        # Enviar para interface web em tempo real
        if self.socketio:
            try:
                self.socketio.emit('log_update', asdict(log_entry))
            except Exception as e:
                logger.error(f"Erro ao enviar log via socketio: {e}")
        
    def get_system_stats(self):
        now = datetime.now()
        connected_count = sum(1 for device in self.detected_devices.values() if device.connected)
        recent_logs = len([log for log in self.logs if 
                          datetime.strptime(log.timestamp, "%H:%M:%S").replace(
                              year=now.year, month=now.month, day=now.day
                          ) > now - timedelta(minutes=5)])
        
        return {
            'valid_addresses_count': len(self.valid_mac_addresses),
            'detected_devices': len(self.detected_devices),
            'connected_devices': connected_count,
            'recent_logs': recent_logs,
            'uptime': str(now - getattr(self, 'start_time', now)),
        }
        
    async def scan_devices(self):
        while self.is_scanning:
            try:
                self.log_message("🔍 Escaneando dispositivos Bluetooth válidos...")
                
                # Scanner com timeout
                devices = await BleakScanner.discover(timeout=8.0, return_adv=True)
                
                current_time = datetime.now()
                found_valid_devices = []
                new_devices = 0
                
                for device_address, (device, adv_data) in devices.items():
                    # FILTRAR: Só processar se o endereço MAC está na lista válida
                    if device_address in self.valid_mac_addresses:
                        is_new_device = device_address not in self.detected_devices
                        
                        device_info = DeviceInfo(
                            address=device_address,
                            name=device.name or f"Dispositivo {device_address[-5:]}",
                            last_seen=current_time.isoformat(),
                            connected=False,
                            rssi=getattr(adv_data, 'rssi', None)
                        )
                        
                        found_valid_devices.append(device_info)
                        
                        if is_new_device:
                            self.detected_devices[device_address] = device_info
                            new_devices += 1
                            self.log_message(f"✅ Dispositivo válido detectado: {device_address} ({device.name}) - RSSI: {device_info.rssi}")
                        else:
                            # Atualizar informações do dispositivo existente
                            existing_device = self.detected_devices[device_address]
                            existing_device.last_seen = current_time.isoformat()
                            existing_device.rssi = device_info.rssi
                            existing_device.name = device_info.name
                
                if new_devices > 0:
                    self.log_message(f"🎯 {new_devices} novos dispositivos válidos encontrados")
                
                # Enviar atualizações para interface web
                if self.socketio:
                    try:
                        self.socketio.emit('devices_update', {
                            'detected_devices': {addr: asdict(device) for addr, device in self.detected_devices.items()},
                            'found_devices': [asdict(device) for device in found_valid_devices],
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
            self.log_message(f"🎯 Procurando por {len(self.valid_mac_addresses)} dispositivos válidos...")
            
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
        
    def connect_single_device(self, device_address):
        """Conecta um dispositivo específico"""
        if device_address not in self.valid_mac_addresses:
            self.log_message(f"❌ Tentativa de conexão negada: {device_address} não está na lista válida", "WARNING")
            return False
            
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
        if device_address in self.detected_devices:
            self.detected_devices[device_address].connected = False
            self.detected_devices[device_address].connection_attempts = 0
            if device_address in self.device_data:
                del self.device_data[device_address]
            self.log_message(f"🔌 Dispositivo {device_address} desconectado", "INFO")
            if self.socketio:
                try:
                    self.socketio.emit('device_disconnected', {'device_address': device_address})
                except Exception as e:
                    logger.error(f"Erro ao emitir desconexão de dispositivo: {e}")
            
    async def connect_device(self, device_address):
        try:
            device_info = self.detected_devices.get(device_address)
            if not device_info:
                self.log_message(f"❌ Dispositivo {device_address} não foi detectado ainda", "WARNING")
                return False
                
            if device_info.connection_attempts >= self.config['max_connection_attempts']:
                self.log_message(f"❌ Máximo de tentativas de conexão atingido para {device_address}", "WARNING")
                return False
                
            device_info.connection_attempts += 1
            self.log_message(f"🔗 Tentativa {device_info.connection_attempts}: Conectando ao {device_address}")
            
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
                        self.log_message(f"✅ Conectado ao dispositivo: {device_address}")
                        
                        # Simular leitura inicial de dados
                        await asyncio.sleep(1)
                        
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
            disconnected_devices = [addr for addr, device in self.detected_devices.items() 
                                  if not device.connected]
            
            if not disconnected_devices:
                self.log_message("ℹ️ Todos os dispositivos válidos já estão conectados")
                return
                
            self.log_message(f"🔗 Conectando {len(disconnected_devices)} dispositivos válidos...")
            
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
        for device_address, device in self.detected_devices.items():
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
        if device_address in self.detected_devices and self.detected_devices[device_address].connected:
            self.log_message(f"📊 Dados solicitados para {device_address}")
        
    def run(self):
        self.start_time = datetime.now()
        self.log_message("🚀 Sistema Bluetooth com Whitelist iniciado")
        self.log_message(f"🎯 Lista de endereços MAC válidos: {self.valid_mac_addresses}")
        
        # Handler para shutdown graceful
        def signal_handler(sig, frame):
            self.log_message("🛑 Encerrando sistema...")
            self.is_scanning = False
            sys.exit(0)
            
        signal.signal(signal.SIGINT, signal_handler)
        
        print(f"\n🎉 Sistema Bluetooth com Whitelist iniciado!")
        print(f"📱 Interface Web: http://localhost:{self.config['web_port']}")
        print(f"🎯 {len(self.valid_mac_addresses)} endereços MAC válidos configurados")
        print(f"📋 Endereços válidos: {', '.join(self.valid_mac_addresses)}")
        print(f"⚠️  Use Ctrl+C para parar o sistema\n")
        
        # Iniciar servidor web
        self.socketio.run(self.app, 
                         host='0.0.0.0', 
                         port=self.config['web_port'], 
                         debug=False,
                         allow_unsafe_werkzeug=True)

# Template da interface web com whitelist
WEB_INTERFACE_TEMPLATE = '''
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sistema Bluetooth - Dispositivos Autorizados</title>
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
        .valid-addresses {
            background: #e8f5e8;
            padding: 15px;
            border-radius: 10px;
            margin-bottom: 15px;
            border-left: 4px solid #28a745;
        }
        .address-list {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 10px;
        }
        .address-tag {
            background: #667eea;
            color: white;
            padding: 4px 8px;
            border-radius: 12px;
            font-family: 'Courier New', monospace;
            font-size: 12px;
            font-weight: bold;
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
        .empty-state {
            text-align: center;
            padding: 40px;
            color: #666;
            font-style: italic;
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
            <h1>🛡️ Sistema Bluetooth - Dispositivos Autorizados</h1>
            <div class="status-bar">
                <div class="status-item">
                    <div class="status-label">Status Scan</div>
                    <div id="scan-status" class="status-value status-disconnected">Parado</div>
                </div>
                <div class="status-item">
                    <div class="status-label">MACs Válidos</div>
                    <div id="valid-count" class="status-value">0</div>
                </div>
                <div class="status-item">
                    <div class="status-label">Detectados</div>
                    <div id="detected-count" class="status-value">0</div>
                </div>
                <div class="status-item">
                    <div class="status-label">Conectados</div>
                    <div id="connected-count" class="status-value">0</div>
                </div>
                <div class="status-item">
                    <div class="status-label">Última Atualização</div>
                    <div id="last-update" class="status-value">--:--:--</div>
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
            <h2>✅ Endereços MAC Autorizados</h2>
            <div id="valid-addresses" class="valid-addresses">
                <strong>Lista de dispositivos autorizados:</strong>
                <div id="address-list" class="address-list">
                    <!-- Endereços serão inseridos aqui -->
                </div>
            </div>
        </div>
        
        <div class="card">
            <h2>📱 Dispositivos Detectados</h2>
            <div id="devices-container" class="devices-grid">
                <div class="empty-state">Nenhum dispositivo autorizado detectado ainda...</div>
            </div>
        </div>
        
        <div class="card">
            <h2>📋 Logs do Sistema</h2>
            <div id="logs-container" class="logs"></div>
        </div>
    </div>

    <script>
        const socket = io();
        let isScanning = false;
        let isConnecting = false;
        let validMacAddresses = [];
        
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
            updateValidAddresses(data.valid_mac_addresses || []);
            updateDevicesDisplay(data.detected_devices);
            updateStats(data.stats);
            data.logs.forEach(log => addLogEntry(log, false));
            updateLastUpdate();
        });
        
        socket.on('devices_update', function(data) {
            updateDevicesDisplay(data.detected_devices);
            if (data.stats) updateStats(data.stats);
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
        
        function updateValidAddresses(addresses) {
            validMacAddresses = addresses;
            const validCount = document.getElementById('valid-count');
            const addressList = document.getElementById('address-list');
            
            validCount.textContent = addresses.length;
            
            if (addresses.length === 0) {
                addressList.innerHTML = '<span style="color: #666; font-style: italic;">Nenhum endereço MAC configurado</span>';
                return;
            }
            
            addressList.innerHTML = addresses.map(address => 
                `<span class="address-tag">${address}</span>`
            ).join('');
        }
        
        function updateDevicesDisplay(devices) {
            const container = document.getElementById('devices-container');
            const detectedCount = document.getElementById('detected-count');
            const connectedCount = document.getElementById('connected-count');
            
            if (!devices || Object.keys(devices).length === 0) {
                container.innerHTML = '<div class="empty-state">Nenhum dispositivo autorizado detectado ainda...</div>';
                detectedCount.textContent = '0';
                connectedCount.textContent = '0';
                return;
            }
            
            const connected = Object.values(devices).filter(d => d.connected).length;
            detectedCount.textContent = Object.keys(devices).length;
            connectedCount.textContent = connected;
            
            container.innerHTML = Object.values(devices).map(device => {
                return `
                    <div class="device-card ${device.connected ? 'device-connected' : 'device-disconnected'}" 
                         data-device="${device.address}">
                        <h4>🛡️ ${device.name}</h4>
                        <div class="device-info">
                            <div><strong>Endereço:</strong> ${device.address}</div>
                            <div><strong>Status:</strong> 
                                <span class="${device.connected ? 'status-connected' : 'status-disconnected'}">
                                    ${device.connected ? 'Conectado' : 'Desconectado'}
                                </span>
                            </div>
                            <div><strong>RSSI:</strong> ${device.rssi || '--'} dBm</div>
                            <div><strong>Última visão:</strong> ${new Date(device.last_seen).toLocaleTimeString()}</div>
                        </div>
                        <div class="device-actions">
                            ${!device.connected ? 
                                `<button class="btn btn-success btn-connect" onclick="connectDevice('${device.address}')">🔗 Conectar</button>` :
                                `<button class="btn btn-danger" onclick="disconnectDevice('${device.address}')">🔌 Desconectar</button>`
                            }
                        </div>
                    </div>
                `;
            }).join('');
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
        
        function updateStats(stats) {
            if (!stats) return;
            // Stats já são atualizados em outras funções
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
        
        // Carregar status inicial
        fetch('/api/status')
            .then(response => response.json())
            .then(data => {
                updateScanStatus(data.is_scanning);
                updateConnectingStatus(data.is_connecting);
                updateValidAddresses(data.valid_mac_addresses || []);
                updateDevicesDisplay(data.detected_devices || {});
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