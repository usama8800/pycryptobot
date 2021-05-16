#!/bin/bash

if [ -z "$1" ]; then
    echo "Need an argument"
    exit 1
fi

upper=$(echo "$1" | sed -e 's/\(.*\)/\U\1/')
lower=$(echo "$1" | sed -e 's/\(.*\)/\L\1/')

cp sample.config.json "$lower.config.json"
sed -i -e "s/SAMPLE/$upper/g" "$lower.config.json"

if grep {} restart-all.sh 1>/dev/null; then
    sed -i -e "s/systemctl restart {}/systemctl restart {$lower}/" restart-all.sh
else
    sed -i -e "s/systemctl restart {\([^}]\)/systemctl restart {$lower,\1/" restart-all.sh
fi

path=$(pwd)
cd /etc/systemd/system
sudo cp "$path/sample.service" "$lower.crypto.service"
sudo sed -i -e "s/Sample/$upper/g" -e "s/sample/$lower/g" "$lower.crypto.service"

systemctl daemon-reload
systemctl start "$lower.crypto.service"
systemctl enable "$lower.crypto.service"
