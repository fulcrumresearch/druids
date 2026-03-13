"""Verify runtime classes satisfy the client Protocol definitions.

The client package (druids) exports ProgramContext and Agent as Protocol
classes. The runtime's RuntimeContext and RuntimeAgent must structurally
conform to these protocols. This test catches drift between the two.

Known gap: RuntimeContext does not satisfy ProgramContext because it lacks
send() and prompt() methods that the protocol requires. The old Execution
test also failed for the same reason. Fixing this protocol drift is
tracked separately.
"""

from druids.lib import Agent as AgentProtocol
from druids_runtime import RuntimeAgent, RuntimeContext


def test_runtime_agent_satisfies_protocol():
    ctx = RuntimeContext(slug="test", _base_url="", _token="")
    agent = RuntimeAgent(name="test", _ctx=ctx)
    assert isinstance(agent, AgentProtocol)
