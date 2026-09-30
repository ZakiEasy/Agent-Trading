import paramiko
import os
import time

hostname = "178.104.133.250"
username = "root"
key_path = os.path.expanduser("~/.ssh/agent_trading_deploy_key")
remote_dir = "/var/www/agent_trading/app"

files_to_upload = [
    "app.py",
    "src/order_guardrails.py",
    "src/risk_manager.py",
    "system_instructions_gem_trading_mean_reversion.md",
    "SYSTEM_PROMPT.md",
    "Dockerfile",
    "docker-compose.yml",
    "requirements.txt",
    "robot_worker.py",
    "CHANGELOG.md",
    "Caddyfile",
]

# We need all src files
for root, dirs, files in os.walk("src"):
    for file in files:
        if file.endswith(".py"):
            files_to_upload.append(os.path.join(root, file))

# We need templates
for root, dirs, files in os.walk("templates"):
    for file in files:
        if file.endswith(".html"):
            files_to_upload.append(os.path.join(root, file))

# We need static files
for root, dirs, files in os.walk("static"):
    for file in files:
        files_to_upload.append(os.path.join(root, file))

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    client.connect(hostname, username=username, key_filename=key_path)
    sftp = client.open_sftp()

    for f in files_to_upload:
        local_path = os.path.join(os.getcwd(), f)
        remote_path = f"{remote_dir}/{f}"
        print(f"Uploading {local_path} to {remote_path}")
        try:
            sftp.stat(os.path.dirname(remote_path))
        except IOError:
            sftp.mkdir(os.path.dirname(remote_path))
        sftp.put(local_path, remote_path)

    sftp.close()
    print("Upload complete. Rebuilding docker containers...")

    commands = [
        # Stop and disable old systemd services
        "systemctl stop agent_trading.service agent_robot.service || true",
        "systemctl disable agent_trading.service agent_robot.service || true",
        # Ensure DB_PASSWORD is in .env
        f"grep -q '^DB_PASSWORD=' {remote_dir}/.env || echo 'DB_PASSWORD=SecurePassTrading2026!' >> {remote_dir}/.env",
        # Stop existing old postgres container if it exists
        "docker stop trading_postgres || true",
        "docker rm trading_postgres || true",
        # Start the new docker-compose stack
        f"cd {remote_dir} && docker compose build",
        f"cd {remote_dir} && docker compose up -d",
    ]
    for cmd in commands:
        print(f"Running: {cmd}")
        stdin, stdout, stderr = client.exec_command(cmd)
        exit_status = stdout.channel.recv_exit_status()
        print("STDOUT:", stdout.read().decode())
        print("STDERR:", stderr.read().decode())

except Exception as e:
    print(f"Error: {e}")
finally:
    client.close()
