## Linux 搭建一些报错修复

[steamcmd](https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz) Linux 依赖 [原文章地址](https://developer.valvesoftware.com/wiki/SteamCMD)

```shell
apt-get install lib32gcc-s1
```

[cs2kz-metamod](https://github.com/KZGlobalTeam/cs2kz-metamod) 插件连不上 MySQL 报错

```shell
sudo apt update
sudo apt install --reinstall libmariadb3 libmariadb-dev mariadb-client
```

```shell
dpkg -L libmariadb3 | grep caching_sha2_password
ls -l /usr/lib/x86_64-linux-gnu/libmariadb3/plugin/caching_sha2_password.so
```

