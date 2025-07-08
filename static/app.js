class BluetoothApp {
    constructor() {
        this.socket = io();
        this.isScanning = false;
        this.isScanningAll = false;
        this.isConnecting = false;
        this.validDevices = [];
        this.currentTab = 'authorized';
        
        this.initializeSocketEvents();
        this.initializeUI();
        this.loadInitialData();
    }
    
    initializeSocketEvents() {
        this.socket.on('connect', () => {
            console.log('Conectado ao servidor');
            this.updateAutoUpdateStatus(true);
        });
        
        this.socket.on('disconnect', () => {
            console.log('Desconectado do servidor');
            this.updateAutoUpdateStatus(false);
        });
        
        this.socket.on('initial_data', (data) => {
            console.log('Dados iniciais recebidos:', data);
            this.updateScanStatus(data.is_scanning, data.is_scanning_all);
            this.updateConnectingStatus(data.is_connecting);
            this.updateValidAddresses(data.valid_devices || []);
            this.updateDevicesDisplay(data.detected_devices, 'authorized');
            this.updateDevicesDisplay(data.all_devices, 'all');
            this.updateStats(data.stats);
            this.updateSystemDevices(data.connected_system_devices || []);
            data.logs.forEach(log => this.addLogEntry(log, false));
        });
        
        this.socket.on('devices_update', (data) => {
            this.updateDevicesDisplay(data.detected_devices, 'authorized');
            if (data.stats) this.updateStats(data.stats);
        });
        
        this.socket.on('all_devices_update', (data) => {
            this.updateDevicesDisplay(data.all_devices, 'all');
            if (data.stats) this.updateStats(data.stats);
        });
        
        this.socket.on('log_update', (log) => {
            this.addLogEntry(log, true);
        });
        
        this.socket.on('serial_data', (data) => {
            this.addSerialData(data);
        });
        
        this.socket.on('scanning_status', (data) => {
            this.updateScanStatus(data.is_scanning, this.isScanningAll);
        });
        
        this.socket.on('scanning_all_status', (data) => {
            this.updateScanStatus(this.isScanning, data.is_scanning);
        });
        
        this.socket.on('connection_status', (data) => {
            this.updateConnectingStatus(data.is_connecting);
        });
        
        this.socket.on('device_connected', (data) => {
            this.addLogEntry({
                timestamp: new Date().toLocaleTimeString(),
                level: 'INFO',
                message: `✅ ${data.device_name} conectado com sucesso`
            }, true);
        });
    }
    
    initializeUI() {
        // Configurar tabs
        document.querySelectorAll('.tab').forEach(tab => {
            tab.addEventListener('click', () => {
                const tabName = tab.getAttribute('onclick').match(/'([^']+)'/)[1];
                this.showTab(tabName);
            });
        });
        
        // Configurar botões
        document.getElementById('start-btn').addEventListener('click', () => this.startScan());
        document.getElementById('start-all-btn').addEventListener('click', () => this.startScanAll());
        document.getElementById('stop-btn').addEventListener('click', () => this.stopScan());
        document.getElementById('connect-all-btn').addEventListener('click', () => this.connectAll());
        document.getElementById('disconnect-all-btn').addEventListener('click', () => this.disconnectAll());
        document.querySelector('button[onclick="clearLogs()"]').addEventListener('click', () => this.clearLogs());
        
        // Carregar portas seriais iniciais
        this.loadSerialPorts();
    }
    
    showTab(tabName) {
        this.currentTab = tabName;
        
        // Atualizar tabs
        document.querySelectorAll('.tab').forEach(tab => tab.classList.remove('active'));
        document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));
        
        document.querySelector(`button[onclick="showTab('${tabName}')"]`).classList.add('active');
        document.getElementById(`tab-${tabName}`).classList.add('active');
    }
    
    updateAutoUpdateStatus(connected) {
        const indicator = document.getElementById('auto-update');
        if (connected) {
            indicator.innerHTML = '🟢 Atualização automática ativa';
            indicator.classList.remove('pulse');
        } else {
            indicator.innerHTML = '🔴 Desconectado';
            indicator.classList.add('pulse');
        }
    }
    
    updateScanStatus(scanning, scanningAll) {
        this.isScanning = scanning;
        this.isScanningAll = scanningAll;
        
        const scanStatus = document.getElementById('scan-status');
        const scanAllStatus = document.getElementById('scan-all-status');
        const startBtn = document.getElementById('start-btn');
        const startAllBtn = document.getElementById('start-all-btn');
        const stopBtn = document.getElementById('stop-btn');
        
        // Status scan autorizados
        if (scanning) {
            scanStatus.textContent = 'Ativo';
            scanStatus.className = 'status-value status-connected pulse';
            startBtn.disabled = true;
        } else {
            scanStatus.textContent = 'Parado';
            scanStatus.className = 'status-value status-disconnected';
            startBtn.disabled = false;
        }
        
        // Status scan completo
        if (scanningAll) {
            scanAllStatus.textContent = 'Ativo';
            scanAllStatus.className = 'status-value status-connected pulse';
            startAllBtn.disabled = true;
        } else {
            scanAllStatus.textContent = 'Parado';
            scanAllStatus.className = 'status-value status-disconnected';
            startAllBtn.disabled = false;
        }
        
        // Botão parar
        stopBtn.disabled = !scanning && !scanningAll;
    }
    
    updateConnectingStatus(connecting) {
        this.isConnecting = connecting;
        const connectAllBtn = document.getElementById('connect-all-btn');
        
        if (connecting) {
            connectAllBtn.innerHTML = '⏳ Conectando...';
            connectAllBtn.disabled = true;
        } else {
            connectAllBtn.innerHTML = '🔗 Conectar Autorizados';
            connectAllBtn.disabled = false;
        }
    }
    
    updateValidAddresses(devices) {
        this.validDevices = devices;
        const validCount = document.getElementById('valid-count');
        const addressList = document.getElementById('address-list');

        validCount.textContent = devices.length;

        if (devices.length === 0) {
            addressList.innerHTML = '<span style="color: #666; font-style: italic;">Nenhum dispositivo autorizado</span>';
            return;
        }

        addressList.innerHTML = devices.map(dev =>
            `<span class="address-tag" title="${dev.address}">${dev.name} (${dev.address})</span>`
        ).join('');
    }
    
    updateSystemDevices(devices) {
        const container = document.getElementById('system-devices-list');
        
        if (!devices || devices.length === 0) {
            container.innerHTML = '<div class="empty-state">Nenhum dispositivo Bluetooth conectado ao sistema</div>';
            return;
        }
        
        container.innerHTML = devices.map(device => `
            <div style="display: inline-block; margin: 5px; padding: 8px 12px; background: #2196f3; color: white; border-radius: 15px; font-size: 12px;">
                💻 ${device.name}
            </div>
        `).join('');
    }
    
    updateDevicesDisplay(devices, type) {
        const container = document.getElementById(`${type === 'authorized' ? 'authorized' : 'all'}-devices-container`);
        const countElement = document.getElementById(`tab-${type === 'authorized' ? 'authorized' : 'all'}-count`);
        
        if (!devices || Object.keys(devices).length === 0) {
            const emptyMsg = type === 'authorized' ? 
                'Nenhum dispositivo autorizado detectado ainda...' : 
                'Inicie o scan completo para ver todos os dispositivos...';
            container.innerHTML = `<div class="empty-state">${emptyMsg}</div>`;
            countElement.textContent = '0';
            return;
        }
        
        const devicesList = Object.values(devices);
        countElement.textContent = devicesList.length;
        
        container.innerHTML = devicesList.map(device => {
            const isAuthorized = device.is_authorized;
            const badgeClass = device.connected ? 'badge-connected' : (isAuthorized ? 'badge-authorized' : '');
            const badgeText = device.connected ? 'Conectado' : (isAuthorized ? 'Autorizado' : '');
            
            return `
                <div class="device-card ${device.connected ? 'device-connected' : ''} ${isAuthorized ? 'device-authorized' : ''}" 
                     data-device="${device.address}">
                    <h4>
                        <span>${device.name}</span>
                        ${badgeText ? `<span class="device-badge ${badgeClass}">${badgeText}</span>` : ''}
                    </h4>
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
                            `<button class="btn btn-success btn-small btn-connect" onclick="app.connectDevice('${device.address}')">🔗 Conectar</button>` :
                            `<button class="btn btn-danger btn-small" onclick="app.disconnectDevice('${device.address}')">🔌 Desconectar</button>`
                        }
                        ${!isAuthorized ? 
                            `<button class="btn btn-info btn-small" onclick="app.addToWhitelist('${device.address}')">➕ Autorizar</button>` : ''
                        }
                    </div>
                </div>
            `;
        }).join('');
    }
    
    updateStats(stats) {
        if (!stats) return;
        
        document.getElementById('detected-count').textContent = stats.detected_devices || 0;
        document.getElementById('all-devices-count').textContent = stats.all_devices_count || 0;
        document.getElementById('connected-count').textContent = stats.connected_devices || 0;
    }
    
    addLogEntry(log, animate = false) {
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
        
        if (animate) {
            setTimeout(() => entry.classList.remove('new-log'), 1000);
        }
        
        // Limitar número de logs
        const maxLogs = 200;
        while (container.children.length > maxLogs) {
            container.removeChild(container.firstChild);
        }
    }
    
    // Métodos de controle da API
    async apiCall(url, method = 'GET', data = null) {
        const options = {
            method: method,
            headers: {
                'Content-Type': 'application/json',
            }
        };
        
        if (data) {
            options.body = JSON.stringify(data);
        }
        
        try {
            const response = await fetch(url, options);
            return await response.json();
        } catch (error) {
            console.error('Erro na API:', error);
            return { success: false, message: 'Erro de conexão' };
        }
    }
    
    async startScan() {
        const data = await this.apiCall('/api/start_scan', 'POST');
        console.log(data.message);
    }
    
    async startScanAll() {
        const data = await this.apiCall('/api/start_scan_all', 'POST');
        console.log(data.message);
    }
    
    async stopScan() {
        const data = await this.apiCall('/api/stop_scan', 'POST');
        console.log(data.message);
    }
    
    async connectDevice(deviceAddress) {
        const data = await this.apiCall(`/api/connect_device/${deviceAddress}`, 'POST');
        console.log(data.message);
    }
    
    async disconnectDevice(deviceAddress) {
        const data = await this.apiCall(`/api/disconnect_device/${deviceAddress}`, 'POST');
        console.log(data.message);
    }
    
    async connectAll() {
        const data = await this.apiCall('/api/connect_all', 'POST');
        console.log(data.message);
    }
    
    async disconnectAll() {
        const data = await this.apiCall('/api/disconnect_all', 'POST');
        console.log(data.message);
    }
    
    async clearLogs() {
        const data = await this.apiCall('/api/clear_logs', 'POST');
        if (data.success) {
            document.getElementById('logs-container').innerHTML = '';
        }
    }
    
    async addToWhitelist(deviceAddress) {
        if (confirm(`Adicionar ${deviceAddress} à lista de dispositivos autorizados?`)) {
            const data = await this.apiCall(`/api/add_to_whitelist/${deviceAddress}`, 'POST');
            if (data.success) {
                this.addLogEntry({
                    timestamp: new Date().toLocaleTimeString(),
                    level: 'INFO',
                    message: data.message
                }, true);
                // Recarregar dados
                this.loadInitialData();
            }
        }
    }
    
    async loadInitialData() {
        try {
            const data = await this.apiCall('/api/status');
            this.updateScanStatus(data.is_scanning, data.is_scanning_all);
            this.updateConnectingStatus(data.is_connecting);
            this.updateValidAddresses(data.valid_devices || []);
            this.updateDevicesDisplay(data.detected_devices || {}, 'authorized');
            this.updateDevicesDisplay(data.all_devices || {}, 'all');
            this.updateStats(data.stats);
            this.updateSystemDevices(data.connected_system_devices || []);
            
            // Carregar logs apenas na inicialização
            if (document.getElementById('logs-container').children.length === 0) {
                data.logs.forEach(log => this.addLogEntry(log, false));
            }
        } catch (error) {
            console.error('Erro ao carregar dados iniciais:', error);
            this.addLogEntry({
                timestamp: new Date().toLocaleTimeString(),
                level: 'ERROR',
                message: 'Erro ao conectar com o servidor'
            }, true);
        }
    }
    
    // Métodos para controle serial
    async loadSerialPorts() {
        try {
            const data = await this.apiCall('/api/serial_ports');
            const select = document.getElementById('serial-port');
            select.innerHTML = '<option value="">Selecione uma porta</option>';
            
            if (data.ports && Array.isArray(data.ports)) {
                data.ports.forEach(port => {
                    const option = document.createElement('option');
                    option.value = port.port;
                    option.textContent = `${port.port} - ${port.description}`;
                    select.appendChild(option);
                });
            }
        } catch (error) {
            console.error('Erro ao carregar portas seriais:', error);
        }
    }
    
    async openSerial() {
        const port = document.getElementById('serial-port').value;
        if (!port) {
            alert('Selecione uma porta serial primeiro');
            return;
        }
        
        try {
            const response = await this.apiCall('/api/open_serial', 'POST', {
                port: port,
                baudrate: 9600
            });
            
            if (response.success) {
                this.addLogEntry({
                    timestamp: new Date().toLocaleTimeString(),
                    level: 'INFO',
                    message: `📡 Porta serial ${port} aberta`
                }, true);
            } else {
                this.addLogEntry({
                    timestamp: new Date().toLocaleTimeString(),
                    level: 'ERROR',
                    message: response.message || 'Erro ao abrir porta serial'
                }, true);
            }
        } catch (error) {
            console.error('Erro ao abrir porta serial:', error);
        }
    }
    
    async closeSerial() {
        try {
            const response = await this.apiCall('/api/close_serial', 'POST');
            if (response.success) {
                this.addLogEntry({
                    timestamp: new Date().toLocaleTimeString(),
                    level: 'INFO',
                    message: '🔌 Porta serial fechada'
                }, true);
            }
        } catch (error) {
            console.error('Erro ao fechar porta serial:', error);
        }
    }
    
    async startReading() {
        try {
            const response = await this.apiCall('/api/start_serial_reading', 'POST');
            if (response.success) {
                this.addLogEntry({
                    timestamp: new Date().toLocaleTimeString(),
                    level: 'INFO',
                    message: '▶️ Leitura contínua iniciada'
                }, true);
            }
        } catch (error) {
            console.error('Erro ao iniciar leitura:', error);
        }
    }
    
    async stopReading() {
        try {
            const response = await this.apiCall('/api/stop_serial_reading', 'POST');
            if (response.success) {
                this.addLogEntry({
                    timestamp: new Date().toLocaleTimeString(),
                    level: 'INFO',
                    message: '⏹️ Leitura contínua parada'
                }, true);
            }
        } catch (error) {
            console.error('Erro ao parar leitura:', error);
        }
    }
    
    addSerialData(data) {
        const container = document.getElementById('serial-data-container');
        
        // Remover mensagem vazia se existir
        const emptyState = container.querySelector('.empty-state');
        if (emptyState) {
            emptyState.remove();
        }
        
        // Criar elemento para dados
        const dataElement = document.createElement('div');
        dataElement.className = 'serial-data-item';
        
        const timestamp = new Date(data.timestamp).toLocaleTimeString();
        dataElement.innerHTML = `
            <div class="serial-data-content">${data.data}</div>
            <div class="serial-data-time">${timestamp}</div>
        `;
        
        // Adicionar no topo
        container.insertBefore(dataElement, container.firstChild);
        
        // Limitar a 100 entradas
        const items = container.querySelectorAll('.serial-data-item');
        if (items.length > 100) {
            items[items.length - 1].remove();
        }
    }
    
    clearSerialData() {
        const container = document.getElementById('serial-data-container');
        container.innerHTML = '<div class="empty-state">Conecte o bastão e inicie a leitura para ver os dados...</div>';
    }
}

// Inicializar aplicação
let app;
document.addEventListener('DOMContentLoaded', () => {
    app = new BluetoothApp();
});

// Manter funções globais para compatibilidade com onclick no HTML
function showTab(tabName) {
    app.showTab(tabName);
}

function startScan() {
    app.startScan();
}

function startScanAll() {
    app.startScanAll();
}

function stopScan() {
    app.stopScan();
}

function connectDevice(deviceAddress) {
    app.connectDevice(deviceAddress);
}

function disconnectDevice(deviceAddress) {
    app.disconnectDevice(deviceAddress);
}

function connectAll() {
    app.connectAll();
}

function disconnectAll() {
    app.disconnectAll();
}

function clearLogs() {
    app.clearLogs();
}

function addToWhitelist(deviceAddress) {
    app.addToWhitelist(deviceAddress);
}

// Funções para controle serial
function loadSerialPorts() {
    app.loadSerialPorts();
}

function openSerial() {
    app.openSerial();
}

function closeSerial() {
    app.closeSerial();
}

function startReading() {
    app.startReading();
}

function stopReading() {
    app.stopReading();
}

function clearSerialData() {
    app.clearSerialData();
}