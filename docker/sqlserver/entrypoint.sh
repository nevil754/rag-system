#!/bin/bash
set -e

# echo "Attendo SQL Server..."
# until /opt/mssql-tools18/bin/sqlcmd \
#     -S localhost \
#     -U SA \
#     -P "$SA_PASSWORD" \
#     -C \
#     -Q "SELECT 1" > /dev/null 2>&1; do
#     sleep 2
# done

for i in 1 2 3; do
    if /opt/mssql-tools18/bin/sqlcmd \
        -S localhost -U SA -P "$SA_PASSWORD" -C \
        -i /docker-entrypoint-initdb.d/init.sql -b; then
        echo "init.sql completato."
        exit 0
    fi
    echo "init.sql fallito (tentativo $i), riprovo tra 10s..."
    sleep 10
done
echo "ATTENZIONE: init.sql non completato dopo 3 tentativi"
exit 1


# echo "SQL Server pronto. Eseguo init.sql..."
# /opt/mssql-tools18/bin/sqlcmd \
#     -S localhost \
#     -U SA \
#     -P "$SA_PASSWORD" \
#     -C \
#     -i /docker-entrypoint-initdb.d/init.sql \
#     -b

# echo "init.sql completato."

