sudo apt update && sudo apt install -y wget

wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh

# 赋予权限
chmod +x Miniconda3-latest-Linux-x86_64.sh

# 执行安装（全程按提示操作）
./Miniconda3-latest-Linux-x86_64.sh

刷新配置
source ~/.bashrc

conda create -n robot_connect python=3.9

conda activate robot_connect

pip install -r requirement.txt
