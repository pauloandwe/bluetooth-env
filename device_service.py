import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional
from bleak import BleakScanner, BleakClient
from dataclasses import dataclass, asdict
from config import Config

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

class DeviceService:
    def __init__(self, config: Config):
        self.config = config
        self.detected_devices: Dict[str, DeviceInfo] = {}
        self.all_devices: Dict[str, DeviceInfo] = {}
        self.device_data: Dict[str, Dict] = {}
        self.logs: List[LogEntry] = []
        self.is_scanning = False
        self.is_scanning_all = False
        self.is_connecting = False
        self.socketio = None
        self.start_time = datetime.now()
    
    def set_socketio(self, socketio):
        self.socketio = socketio
    
    def log_message(self, message: str, level: str = "INFO", device_address: Optional[str] = None):
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
        now = datetime.now()
        connected_count = sum(1 for device in self.detected_devices.values() if device.connected)
        all_connected_count = sum(1 for device in self.all_devices.values() if device.connected)
        
        return {
            'valid_addresses_count': len(self.config.valid_devices),
            'detected_devices': len(self.detected_devices),
            'all_devices_count': len(self.all_devices),
            'connected_devices': connected_count,
            'all_connected_devices': all_connected_count,
            'uptime': str(now - self.start_time),
        }
    
    async def scan_devices(self, authorized_only=True):
        while (self.is_scanning and authorized_only) or (self.is_scanning_all and not authorized_only):
            try:
                scan_type = "autorizados" if authorized_only else "todos os"
                self.log_message(f"🔍 Escaneando {scan_type} dispositivos Bluetooth...")
                
                devices = await BleakScanner.discover(timeout=8.0, return_adv=True)
                current_time = datetime.now()
                found_devices = []
                new_devices = 0
                
                for device_address, (device, adv_data) in devices.items():
                    is_authorized = device_address in self.config.valid_mac_addresses
                    stored_name = self._get_stored_device_name(device_address)

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
                    
                    target_dict = self.detected_devices if authorized_only else self.all_devices
                    
                    if device_address not in target_dict:
                        target_dict[device_address] = device_info
                        new_devices += 1
                        
                        status_icon = "✅" if is_authorized else "📱"
                        auth_text = "autorizado" if is_authorized else "detectado"
                        self.log_message(
                            f"{status_icon} Dispositivo {auth_text}: {device_address} ({device_info.name}) - RSSI: {device_info.rssi}")
                    else:
                        existing_device = target_dict[device_address]
                        existing_device.last_seen = current_time.isoformat()
                        existing_device.rssi = device_info.rssi
                        existing_device.name = device_info.name
                
                if new_devices > 0:
                    device_type = "autorizados" if authorized_only else "novos"
                    self.log_message(f"🎯 {new_devices} {device_type} dispositivos encontrados")
                
                self._emit_device_update(found_devices, authorized_only)
                await asyncio.sleep(self.config.get('scan_interval'))
                
            except Exception as e:
                self.log_message(f"❌ Erro durante escaneamento: {e}", "ERROR")
                await asyncio.sleep(3)
    
    def _get_stored_device_name(self, device_address: str) -> Optional[str]:
        for device in self.config.valid_devices:
            if device['address'] == device_address:
                return device.get('name')
        return None
    
    def _emit_device_update(self, found_devices: List[DeviceInfo], authorized_only: bool):
        if not self.socketio:
            return
            
        try:
            update_data = {
                'found_devices': [asdict(device) for device in found_devices],
                'stats': self.get_system_stats()
            }
            
            if authorized_only:
                update_data['detected_devices'] = {
                    addr: asdict(device) for addr, device in self.detected_devices.items()
                }
                self.socketio.emit('devices_update', update_data)
            else:
                update_data['all_devices'] = {
                    addr: asdict(device) for addr, device in self.all_devices.items()
                }
                self.socketio.emit('all_devices_update', update_data)
        except Exception as e:
            logger.error(f"Erro ao enviar atualização de dispositivos: {e}")
    
    async def connect_device(self, device_address: str) -> bool:
        try:
            device_info = (self.detected_devices.get(device_address) or 
                          self.all_devices.get(device_address))
            
            if not device_info:
                self.log_message(f"❌ Dispositivo {device_address} não foi detectado ainda", "WARNING")
                return False
                
            if device_info.connection_attempts >= self.config.get('max_connection_attempts'):
                self.log_message(f"❌ Máximo de tentativas de conexão atingido para {device_address}", "WARNING")
                return False
                
            device_info.connection_attempts += 1
            self.log_message(f"🔗 Tentativa {device_info.connection_attempts}: Conectando ao {device_address}")
            
            self.is_connecting = True
            
            try:
                async with BleakClient(device_address, timeout=self.config.get('connection_timeout')) as client:
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
                        
                        # Iniciar loop de leitura contínua de dados
                        await self._start_data_reading(client, device_address, device_info.name)
                        
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
    
    def disconnect_device(self, device_address: str):
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
    
    async def connect_all_devices(self):
        disconnected_devices = [addr for addr, device in self.detected_devices.items() 
                              if not device.connected]
        
        if not disconnected_devices:
            self.log_message("ℹ️ Todos os dispositivos válidos já estão conectados")
            return
            
        self.log_message(f"🔗 Conectando {len(disconnected_devices)} dispositivos válidos...")
        
        tasks = [self.connect_device(addr) for addr in disconnected_devices]
                
        if tasks:
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as e:
                self.log_message(f"Erro ao conectar dispositivos: {e}", "ERROR")
    
    def disconnect_all_devices(self):
        disconnected_count = 0
        
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
    
    def clear_logs(self):
        self.logs.clear()
        self.log_message("Logs limpos", "INFO")
    
    async def _start_data_reading(self, client: BleakClient, device_address: str, device_name: str):
        """Inicia a leitura contínua de dados do dispositivo conectado"""
        try:
            self.log_message(f"📡 Iniciando leitura de dados do dispositivo {device_name} ({device_address})")
            
            # Obter todos os serviços disponíveis
            services = await client.get_services()
            
            # Processar cada serviço e suas características
            for service in services:
                self.log_message(f"🔍 Serviço encontrado: {service.uuid}")
                
                for characteristic in service.characteristics:
                    char_uuid = characteristic.uuid
                    char_properties = characteristic.properties
                    
                    self.log_message(f"  📋 Característica: {char_uuid} - Propriedades: {char_properties}")
                    
                    # Se a característica suporta notificações, configurar callback
                    if "notify" in char_properties:
                        try:
                            await client.start_notify(char_uuid, 
                                lambda sender, data, addr=device_address, name=device_name: 
                                self._handle_device_data(addr, name, sender, data))
                            self.log_message(f"🔔 Notificações habilitadas para {char_uuid}")
                        except Exception as e:
                            self.log_message(f"❌ Erro ao habilitar notificações para {char_uuid}: {e}", "ERROR")
                    
                    # Se a característica suporta leitura, fazer leitura inicial
                    if "read" in char_properties:
                        try:
                            data = await client.read_gatt_char(char_uuid)
                            if data:
                                self._handle_device_data(device_address, device_name, char_uuid, data)
                        except Exception as e:
                            self.log_message(f"❌ Erro ao ler característica {char_uuid}: {e}", "ERROR")
            
            # Manter a conexão ativa enquanto o dispositivo estiver conectado
            device_info = (self.detected_devices.get(device_address) or 
                          self.all_devices.get(device_address))
            
            if device_info:
                while device_info.connected and client.is_connected:
                    await asyncio.sleep(1)  # Verificar conexão a cada segundo
                    
                self.log_message(f"🔌 Parando leitura de dados do dispositivo {device_name} ({device_address})")
                
        except Exception as e:
            self.log_message(f"❌ Erro na leitura de dados do dispositivo {device_address}: {e}", "ERROR")
    
    def _handle_device_data(self, device_address: str, device_name: str, sender, data: bytes):
        """Processa os dados recebidos do dispositivo"""
        try:
            # Converter dados para string (assumindo UTF-8, com fallback para hex)
            try:
                data_str = data.decode('utf-8').strip()
            except UnicodeDecodeError:
                data_str = data.hex()
            
            if data_str:  # Só processar se houver dados
                # Log no console
                print(f"📱 [{device_name}] Dados recebidos: {data_str}")
                
                # Log no sistema
                self.log_message(f"📱 [{device_name}] Dados: {data_str}", "INFO", device_address)
                
                # Armazenar dados do dispositivo
                if device_address not in self.device_data:
                    self.device_data[device_address] = {
                        'name': device_name,
                        'data_history': [],
                        'last_data': None,
                        'last_update': None
                    }
                
                # Adicionar ao histórico
                timestamp = datetime.now().isoformat()
                self.device_data[device_address]['data_history'].append({
                    'timestamp': timestamp,
                    'data': data_str,
                    'sender': str(sender)
                })
                
                # Manter apenas os últimos 100 registros
                if len(self.device_data[device_address]['data_history']) > 100:
                    self.device_data[device_address]['data_history'] = \
                        self.device_data[device_address]['data_history'][-100:]
                
                # Atualizar último dado
                self.device_data[device_address]['last_data'] = data_str
                self.device_data[device_address]['last_update'] = timestamp
                
                # Emitir dados via WebSocket se disponível
                if self.socketio:
                    try:
                        self.socketio.emit('device_data', {
                            'device_address': device_address,
                            'device_name': device_name,
                            'data': data_str,
                            'timestamp': timestamp,
                            'sender': str(sender)
                        })
                    except Exception as e:
                        logger.error(f"Erro ao emitir dados do dispositivo: {e}")
                
        except Exception as e:
            self.log_message(f"❌ Erro ao processar dados do dispositivo {device_address}: {e}", "ERROR")