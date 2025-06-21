#!/bin/sh

set -e

# Start the config file
cat > config.json <<EOF
{
  "api_token": "${BUNQ_API_TOKEN}",
  "personal_access_token": "${YNAB_PERSONAL_ACCESS_TOKEN}",
  "accounts": [
EOF

# Loop through possible account indices
i=1
first=1
while [ -n "$(eval echo \${BUNQ_ACCOUNT_NAME_$i})" ]; do
  # Add comma if not the first account
  if [ $first -eq 0 ]; then
    echo "," >> config.json
  fi
  first=0

  bunq_account_name=$(eval echo \${BUNQ_ACCOUNT_NAME_$i})
  ynab_budget_name=$(eval echo \${YNAB_BUDGET_NAME_$i})
  ynab_account_name=$(eval echo \${YNAB_ACCOUNT_NAME_$i})

  cat >> config.json <<EOF
    {
      "bunq_account_name": "$bunq_account_name",
      "ynab_budget_name": "$ynab_budget_name",
      "ynab_account_name": "$ynab_account_name"
    }
EOF

  i=$((i+1))
done

# Close the JSON
cat >> config.json <<EOF
  ]
}
EOF

# If EXTERNAL_PORT is set, add it as an argument and print a log message
if [ -n "$EXTERNAL_PORT" ]; then
  echo "Using port $EXTERNAL_PORT"
  exec python3 auto_sync.py --external-port "$EXTERNAL_PORT"
else
  echo "No external port specified, running without --external-port"
  exec python3 auto_sync.py
fi
