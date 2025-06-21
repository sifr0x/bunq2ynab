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

if [ -n "$PORT" ]; then
  exec python3 auto_sync.py --external-port "$PORT"
else
  exec python3 auto_sync.py
fi
