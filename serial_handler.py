import serial
import logging
from serial.tools import list_ports
from typing import List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class SerialPortInfo:
    port: str
    description: str
    hwid: str

class SerialHandler:
    def __init__(self):
        self.serial_conn = None
        self.log_callback = None
        self.data_callback = None
        self.is_reading = False
    
    def set_log_callback(self, callback):
        self.log_callback = callback
    
    def set_data_callback(self, callback):
        self.data_callback = callback
    
    def _log(self, message: str, level: str = "INFO"):
        if self.log_callback:
            self.log_callback(message, level)
        else:
            logger.log(getattr(logging, level), message)
    
    def list_serial_ports(self) -> List[SerialPortInfo]:
        ports = []
        for p in list_ports.comports():
            ports.append(SerialPortInfo(
                port=p.device,
                description=p.description,
                hwid=p.hwid
            ))
        return ports

    def open_serial_port(self, port: str, baudrate: int = 9600, timeout: float = 1.0) -> bool:
        try:
            self.serial_conn = serial.Serial(port, baudrate=baudrate, timeout=timeout)
            self._log(f"Porta serial {port} aberta", "INFO")
            return True
        except Exception as e:
            self._log(f"Erro ao abrir porta serial {port}: {e}", "ERROR")
            self.serial_conn = None
            return False

    def close_serial_port(self):
        if self.serial_conn:
            try:
                self.serial_conn.close()
                self._log("Porta serial fechada", "INFO")
            except Exception as e:
                self._log(f"Erro ao fechar porta serial: {e}", "ERROR")
        self.serial_conn = None

    def send_serial_data(self, data: str) -> bool:
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.write(data.encode())
                return True
            except Exception as e:
                self._log(f"Erro ao enviar dados pela serial: {e}", "ERROR")
        return False

    def read_serial_line(self) -> Optional[str]:
        if self.serial_conn and self.serial_conn.is_open:
            try:
                line = self.serial_conn.readline().decode(errors='ignore').strip()
                if line and self.data_callback:
                    self.data_callback(line)
                return line
            except Exception as e:
                self._log(f"Erro ao ler da serial: {e}", "ERROR")
        return None
    
    def start_continuous_reading(self):
        import threading
        if not self.is_reading and self.serial_conn and self.serial_conn.is_open:
            self.is_reading = True
            self._log("Iniciando leitura contínua da serial", "INFO")
            
            def read_loop():
                while self.is_reading and self.serial_conn and self.serial_conn.is_open:
                    try:
                        if self.serial_conn.in_waiting > 0:
                            line = self.serial_conn.readline().decode(errors='ignore').strip()
                            if line:
                                self._log(f"Dados lidos: {line}", "INFO")
                                if self.data_callback:
                                    self.data_callback(line)
                    except Exception as e:
                        self._log(f"Erro na leitura contínua: {e}", "ERROR")
                        break
                    import time
                    time.sleep(0.1)
                self.is_reading = False
            
            thread = threading.Thread(target=read_loop)
            thread.daemon = True
            thread.start()
    
    def stop_continuous_reading(self):
        self.is_reading = False
        self._log("Parando leitura contínua da serial", "INFO")