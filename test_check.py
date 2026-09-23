import sys
sys.path.insert(0, 'C:/Users/rishi/hermus-agent-free')
src = open('C:/Users/rishi/hermus-agent-free/core/agent.py', encoding='utf-8').read()
import re
print('result.get("error_code") == "APPROVAL_REQUIRED"', 'result.get("error_code") == "APPROVAL_REQUIRED"' in src)
m = re.search(r'emit\\(\s*"approval_required"', src)
print('emit approval_required:', bool(m))
print('waiting_for_approval:', '"waiting_for_approval"' in src)
print('status check:', '"status": "waiting_for_approval" if pending_approval else "done"' in src)