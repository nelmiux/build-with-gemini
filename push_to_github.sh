#!/usr/bin/env bash
set -euo pipefail

echo "=========================================================="
echo "Push NovaSmart Governance App to GitHub"
echo "Target: https://github.com/nelmiux/build-with-gemini"
echo "=========================================================="

if [ -z "${GITHUB_TOKEN:-}" ]; then
  read -s -p "Enter your GitHub Personal Access Token (PAT): " GITHUB_TOKEN
  echo ""
fi

if [ -z "$GITHUB_TOKEN" ]; then
  echo "Error: Token cannot be empty."
  exit 1
fi

echo "Pushing main branch to GitHub..."
git push "https://${GITHUB_TOKEN}@github.com/nelmiux/build-with-gemini.git" main

echo ""
echo "✔ Successfully pushed to https://github.com/nelmiux/build-with-gemini !"
