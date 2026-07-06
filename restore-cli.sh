apt-get install python3 python3-venv -y
python3 -m venv /opt/teams-cli-venv
/opt/teams-cli-venv/bin/pip install -r workshop/teams-management/cli/requirements.txt
chmod +x workshop/teams-management/cli/teams-cli.py
ln -s workshop/teams-management/cli/teams-cli.py /usr/local/bin/teams-cli
