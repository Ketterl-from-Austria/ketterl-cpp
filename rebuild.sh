#!/bin/bash
# rebuild.sh – läuft auf dem Pi
# Wird vom Cron-Job aufgerufen: git pull → HTML neu generieren

set -e
REPO=/home/kettleradm/ketterl
LOG=/tmp/ketterl-rebuild.log

echo "[$(date)] Pull gestartet..." >> $LOG

cd $REPO

# Prüfen ob neue Version auf GitHub
git fetch origin main --quiet
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    echo "[$(date)] Keine Änderung." >> $LOG
    exit 0
fi

# Neu ziehen
git pull origin main --quiet
echo "[$(date)] Pulled: $(git log -1 --format='%h %s')" >> $LOG

# HTML neu generieren
python3 gen_villa_real.py >> $LOG 2>&1

echo "[$(date)] Fertig." >> $LOG
