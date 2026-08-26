#!/usr/bin/env python3
"""
Update GitHub Actions workflow to add IndicVoice installation step.
Run this script to automatically update .github/workflows/daily_short.yml
"""

import sys
from pathlib import Path
import yaml

def update_workflow():
    workflow_path = Path(".github/workflows/daily_short.yml")
    
    if not workflow_path.exists():
        print(f"❌ Workflow file not found: {workflow_path}")
        sys.exit(1)
    
    print(f"📖 Reading workflow: {workflow_path}")
    with open(workflow_path, 'r') as f:
        content = f.read()
    
    # Check if IndicVoice step already exists
    if "Install IndicVoice" in content:
        print("✅ IndicVoice step already present in workflow")
        return
    
    # Find the "Install Python dependencies" step and add IndicVoice after it
    old_pattern = """      - name: Install Python dependencies
        run: pip install -r requirements.txt

      - name: Run pipeline"""
    
    new_pattern = """      - name: Install Python dependencies
        run: pip install -r requirements.txt

      - name: Install IndicVoice
        run: pip install git+https://github.com/Bindkushal/indic-voice.git
        continue-on-error: true

      - name: Run pipeline"""
    
    if old_pattern not in content:
        print("❌ Could not find expected workflow pattern")
        print("Please manually add the IndicVoice installation step")
        sys.exit(1)
    
    updated_content = content.replace(old_pattern, new_pattern)
    
    # Also update timeout
    updated_content = updated_content.replace(
        "    timeout-minutes: 8",
        "    timeout-minutes: 12"
    )
    
    print("✏️  Updating workflow file...")
    with open(workflow_path, 'w') as f:
        f.write(updated_content)
    
    print("✅ Workflow updated successfully!")
    print("   - Added IndicVoice installation step")
    print("   - Increased timeout to 12 minutes")
    print("\n🚀 Next: Commit this change and run workflow manually")

if __name__ == "__main__":
    update_workflow()
