#!/bin/bash

if [ -z "$1" ]; then
    echo "Need an argument"
    exit 1
fi

upper=$(echo "$1" | sed -e 's/\(.*\)/\U\1/')
lower=$(echo "$1" | sed -e 's/\(.*\)/\L\1/')

cp sample.config.json "$lower.config.json"
sed -i -e "s/SAMPLE/$upper/g" "$lower.config.json"

grep , restart-all.sh
if [ "$?" -eq 0 ]; then
    sed -i -e "s/systemctl restart {\([^}]\)/systemctl restart {$lower,\1/" restart-all.sh
else
    sed -i -e "s/systemctl restart {}/systemctl restart {$lower}/" restart-all.sh
fi

$path=$(pwd)
cd /etc/systemd/system
sudo cp "$path/sample.service" "$lower.service"
sudo sed -i -e "s/Sample/$upper/g" -e "s/sample/$lower/g" "$lower.service"

systemctl daemon-reload
systemctl start "$lower.service"
systemctl enable "$lower.service"
