#!/usr/bin/env bash
"""
Complete Daily Shorts v3 Setup & Deployment Script
Clones repo, updates workflow, commits, and prepares for manual test run
Usage: bash setup_and_deploy.sh
"""

set -e  # Exit on any error

echo "=================================================="
echo "Daily Shorts v3 - Automated Setup & Deployment"
echo "=================================================="

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Step 1: Clone the repository
echo -e "${BLUE}[1/5] Cloning repository...${NC}"
if [ -d "daily-shorts" ]; then
    echo "Repository already exists, using existing clone"
    cd daily-shorts
    git pull origin main
else
    git clone https://github.com/shuklabhagwan185-dot/daily-shorts.git
    cd daily-shorts
fi

# Step 2: Run the workflow updater
echo -e "${BLUE}[2/5] Running workflow updater...${NC}"
python3 update_workflow.py

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ Workflow updated successfully${NC}"
else
    echo -e "${YELLOW}⚠️  Workflow update had issues, continuing...${NC}"
fi

# Step 3: Verify workflow changes
echo -e "${BLUE}[3/5] Verifying workflow changes...${NC}"
if grep -q "Install IndicVoice" .github/workflows/daily_short.yml; then
    echo -e "${GREEN}✅ IndicVoice step verified in workflow${NC}"
else
    echo -e "${YELLOW}⚠️  Could not verify IndicVoice step${NC}"
fi

if grep -q "timeout-minutes: 12" .github/workflows/daily_short.yml; then
    echo -e "${GREEN}✅ Timeout increased to 12 minutes${NC}"
else
    echo -e "${YELLOW}⚠️  Timeout not updated${NC}"
fi

# Step 4: Commit changes
echo -e "${BLUE}[4/5] Committing workflow changes...${NC}"
git config user.name "Local Automation"
git config user.email "automation@local"

if git diff --quiet .github/workflows/daily_short.yml; then
    echo "No changes to commit"
else
    git add .github/workflows/daily_short.yml
    git commit -m "Workflow: Add IndicVoice installation and increase timeout"
    echo -e "${GREEN}✅ Changes committed${NC}"
fi

# Step 5: Push to remote
echo -e "${BLUE}[5/5] Pushing to GitHub...${NC}"
git push origin main
echo -e "${GREEN}✅ Changes pushed to GitHub${NC}"

# Summary
echo ""
echo "=================================================="
echo -e "${GREEN}✅ SETUP COMPLETE${NC}"
echo "=================================================="
echo ""
echo "Next Steps:"
echo "1. Go to: https://github.com/shuklabhagwan185-dot/daily-shorts/actions"
echo "2. Click 'Daily Motivational Short' workflow"
echo "3. Click 'Run workflow' button"
echo "4. Watch the logs for:"
echo "   - 'Attempting to generate voiceover with IndicVoice...'"
echo "   - 'IndicVoice audio generated successfully' (or fallback to Edge TTS)"
echo ""
echo "Repository directory: $(pwd)"
echo ""
