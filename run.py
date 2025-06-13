#!/usr/bin/env python3
"""
Script de inicialização do Bluetooth Manager
Verifica dependências e inicia o sistema
"""

import sys
import os
import subprocess
import json
from pathlib import Path

def check_python_version():
    """Verifica se a versão do Python é adequada"""
    if sys.version_info < (3, 8):
        print("❌ Erro: Python 3.8 ou superior é necessário")
        print(f"   Versão atual: {sys.version}")
        return False
    print(f"✅ Python {sys.version.split()[0]} - OK")
    return True

def check_dependencies():
    """Verifica se as dependências estão instaladas"""
    required_packages = [
        'flask', 'flask_socketio', 'bleak', 'requests', 'serial'
    ]
    
    missing_packages = []
    
    for package in required_packages:
        try:
            __import__(package)
            print(f"✅ {package} - OK")
        except ImportError:
            missing_packages.append(package)
            print(f"❌ {package} - FALTANDO")
    
    if missing_packages:
        print(f"\n📦 Instalando dependências faltantes...")
        try:
            subprocess.check_call([
                sys.executable, "-m", "pip", "install", "-r", "requirements.txt"
            ])
            print("✅ Dependências instaladas com sucesso!")
            return True
        except subprocess.CalledProcessError:
            print("❌ Erro ao instalar dependências")
            print("   Execute manualmente: pip install -r requirements.txt")
            return False
    
    return True

def check_project_structure():
    """Verifica e cria a estrutura de pastas necessária"""
    templates_dir = Path("templates")
    
    if not templates_dir.exists():
        print("📁 Criando diretório templates...")
        templates_dir.mkdir()
        print("✅ Diretório templates criado")
    else:
        print("✅ Diretório templates - OK")
    
    # Verificar se existe o arquivo HTML
    index_file = templates_dir / "index.html"
    if not index_file.exists():
        print("❌ Arquivo templates/index.html não encontrado")
        print("   Certifique-se de que o arquivo index.html está na pasta templates/")
        return False
    
    print("✅ templates/index.html - OK")
    return True

def check_config_file():
    """Verifica e cria arquivo de configuração se necessário"""
    config_file = Path("bluetooth_config.json")
    
    if not config_file.exists():
        print("📋 Criando arquivo de configuração padrão...")
        default_config = {
            "socket_port": 8888,
            "web_port": 5001,
            "scan_interval": 5,
            "data_update_interval": 2,
            "connection_timeout": 10,
            "max_connection_attempts": 3,
            "valid_devices": [
                {"address": "AA:BB:CC:DD:EE:FF", "name": "Device 1"},
                {"address": "11:22:33:44:55:66", "name": "Device 2"},
                {"address": "77:88:99:AA:BB:CC", "name": "Device 3"}
            ]
        }
        
        try:
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=2, ensure_ascii=False)
            print("✅ Arquivo de configuração criado")
            print("   Edite bluetooth_config.json para configurar seus dispositivos")
        except Exception as e:
            print(f"❌ Erro ao criar arquivo de configuração: {e}")
            return False
    else:
        print("✅ bluetooth_config.json - OK")
    
    return True

def show_startup_info():
    """Mostra informações de inicialização"""
    print("\n" + "="*60)
    print("🔧 BLUETOOTH MANAGER - SISTEMA COMPLETO")
    print("="*60)
    print("📱 Interface Web: http://localhost:5001")
    print("🎯 Funcionalidades:")
    print("   • Scan de dispositivos autorizados")
    print("   • Scan completo de todos os dispositivos")
    print("   • Monitoramento de dispositivos do sistema")
    print("   • Whitelist dinâmica")
    print("   • Conexão/desconexão inteligente")
    print("   • Logs em tempo real")
    print("\n⚠️  Use Ctrl+C para parar o sistema")
    print("="*60 + "\n")

def main():
    """Função principal"""
    print("🚀 Iniciando Bluetooth Manager...\n")
    
    # Verificações pré-inicialização
    checks = [
        ("Versão do Python", check_python_version),
        ("Dependências", check_dependencies),
        ("Estrutura do projeto", check_project_structure),
        ("Arquivo de configuração", check_config_file)
    ]
    
    for check_name, check_func in checks:
        print(f"\n🔍 Verificando {check_name}...")
        if not check_func():
            print(f"\n❌ Falha na verificação: {check_name}")
            print("   Corrija os problemas acima antes de continuar.")
            sys.exit(1)
    
    print("\n✅ Todas as verificações concluídas com sucesso!")
    
    # Mostrar informações de inicialização
    show_startup_info()
    
    # Iniciar o sistema
    try:
        from bluetooth_web_manager import BluetoothManager
        app = BluetoothManager()
        app.run()
    except KeyboardInterrupt:
        print("\n\n🛑 Sistema encerrado pelo usuário")
    except ImportError as e:
        print(f"\n❌ Erro ao importar módulo: {e}")
        print("   Verifique se o arquivo bluetooth_web_manager.py existe")
    except Exception as e:
        print(f"\n❌ Erro inesperado: {e}")
        print("   Verifique os logs para mais detalhes")

if __name__ == "__main__":
    main()