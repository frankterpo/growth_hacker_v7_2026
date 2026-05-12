#!/usr/bin/env bash
# Scrape v7labs customer domains
URL="https://www.v7labs.com/customer-stories"
# Fetch the page
content=$(curl -s "$URL")
# Find the JSON that holds the story data
# In Framer sites, the data is often in a <script> with type="application/json" or data-framer-component="CustomerStories".
# Searching for "data-framer-client" attribute
json=$(echo "$content" | grep -oP '(?<=<script[^>]*data-framer-client="customers">)[^<]+(?=</script>)' | head -n 1)
if [[ -z "$json" ]]; then
  echo "Could not find JSON block"
  exit 1
fi
# Pretty print with jq to extract domains
jq -r '.items[] | .domain' <<< "$json"
