"""
Agent Specialization - Role-based agent types with specialized capabilities.

Each role has:
- Specific prompt templates
- Specialized tools
- Unique capabilities
- Custom behavior
"""

from __future__ import annotations

from typing import Any, Optional
from enum import Enum

from core.log import get_logger
from .agent import Agent, AgentConfig, AgentRole

logger = get_logger(__name__)


# Role definitions with their capabilities
ROLE_DEFINITIONS = {
    AgentRole.RESEARCHER: {
        "name": "Researcher",
        "description": "Specialized in information gathering, analysis, and research",
        "capabilities": [
            "web_search",
            "document_analysis",
            "data_collection",
            "trend_analysis",
            "citation_management",
        ],
        "prompt_template": """
You are a Researcher AI assistant. Your role is to:
1. Gather comprehensive information on requested topics
2. Analyze data and identify patterns
3. Verify facts and provide accurate citations
4. Summarize findings clearly
5. Identify gaps in knowledge

Current task: {task}
Previous findings: {context}

Provide detailed, well-researched responses with proper citations.
""",
    },
    
    AgentRole.CODER: {
        "name": "Coder",
        "description": "Specialized in software development, coding, and debugging",
        "capabilities": [
            "code_generation",
            "code_analysis",
            "debugging",
            "testing",
            "refactoring",
            "build_automation",
        ],
        "prompt_template": """
You are a Coder AI assistant. Your role is to:
1. Write clean, efficient, and well-documented code
2. Analyze existing code for issues and improvements
3. Debug problems and provide solutions
4. Create and run tests
5. Refactor code for better performance

Current task: {task}
Code context: {context}

Provide code solutions with explanations and best practices.
""",
    },
    
    AgentRole.VERIFIER: {
        "name": "Verifier",
        "description": "Specialized in verification, validation, and quality assurance",
        "capabilities": [
            "code_verification",
            "fact_checking",
            "quality_assurance",
            "security_audit",
            "performance_testing",
        ],
        "prompt_template": """
You are a Verifier AI assistant. Your role is to:
1. Verify the accuracy of information and code
2. Check for errors, bugs, and vulnerabilities
3. Validate that requirements are met
4. Perform quality assurance checks
5. Provide detailed verification reports

Item to verify: {task}
Verification criteria: {criteria}

Be thorough and precise in your verification.
""",
    },
    
    AgentRole.CHAIR: {
        "name": "Chair",
        "description": "Orchestrates multi-agent collaboration and decision making",
        "capabilities": [
            "task_decomposition",
            "agent_coordination",
            "consensus_building",
            "decision_making",
            "progress_tracking",
        ],
        "prompt_template": """
You are a Chair AI assistant. Your role is to:
1. Break down complex tasks into manageable parts
2. Coordinate multiple agents working together
3. Build consensus among agents
4. Make final decisions based on agent input
5. Track progress and ensure completion

Task: {task}
Available agents: {agents}

Provide clear direction and coordination.
""",
    },
    
    AgentRole.CRITIC: {
        "name": "Critic",
        "description": "Provides critical analysis and constructive feedback",
        "capabilities": [
            "critical_analysis",
            "flaw_identification",
            "improvement_suggestions",
            "risk_assessment",
            "quality_evaluation",
        ],
        "prompt_template": """
You are a Critic AI assistant. Your role is to:
1. Critically analyze work and proposals
2. Identify flaws, weaknesses, and potential issues
3. Provide constructive feedback for improvement
4. Assess risks and benefits
5. Evaluate overall quality

Item to critique: {task}
Evaluation criteria: {criteria}

Be thorough but constructive in your criticism.
""",
    },
    
    AgentRole.SYNTHESIZER: {
        "name": "Synthesizer",
        "description": "Combines and synthesizes information from multiple sources",
        "capabilities": [
            "information_synthesis",
            "summary_generation",
            "conflict_resolution",
            "consensus_building",
            "report_generation",
        ],
        "prompt_template": """
You are a Synthesizer AI assistant. Your role is to:
1. Combine information from multiple sources
2. Identify common themes and patterns
3. Resolve conflicts and inconsistencies
4. Build consensus from diverse inputs
5. Generate comprehensive summaries and reports

Information to synthesize: {task}
Sources: {sources}

Provide clear, comprehensive syntheses.
""",
    },
    
    AgentRole.TOOL_RUNNER: {
        "name": "Tool Runner",
        "description": "Executes tools and commands on behalf of other agents",
        "capabilities": [
            "command_execution",
            "tool_usage",
            "file_operations",
            "web_operations",
            "api_calls",
        ],
        "prompt_template": """
You are a Tool Runner AI assistant. Your role is to:
1. Execute commands and tools safely
2. Use the appropriate tool for each task
3. Handle errors and edge cases
4. Return clear results
5. Follow all safety guidelines

Task: {task}
Available tools: {tools}

Execute the task using the appropriate tools.
""",
    },
}


class SpecializedAgent(Agent):
    """
    Base class for specialized agents with role-specific behavior.
    """
    
    def __init__(self, role: AgentRole, **kwargs):
        # Set role-specific defaults
        role_def = ROLE_DEFINITIONS.get(role, {})
        
        # Create config with role defaults
        config_kwargs = {
            "role": role,
            "name": kwargs.get("name") or f"{role_def.get('name', 'Agent')}-{kwargs.get('model', 'default')[:4]}",
        }
        config_kwargs.update(kwargs)
        
        super().__init__(config=AgentConfig(**config_kwargs))
        
        self.role_definition = role_def
        self.capabilities = role_def.get("capabilities", [])
    
    def get_prompt(self, task: str, context: str = "") -> str:
        """Get the role-specific prompt for a task."""
        template = self.role_definition.get("prompt_template", "")
        return template.format(task=task, context=context)
    
    async def execute_specialized_task(self, task: str, context: dict = None) -> str:
        """
        Execute a task using role-specific logic.
        
        Args:
            task: The task to execute
            context: Additional context
        
        Returns:
            Result of the task
        """
        # This will be overridden by specific agent types
        return await self.run_task(task)


class Researcher(SpecializedAgent):
    """
    Researcher Agent - Specialized in information gathering and analysis.
    """
    
    def __init__(self, **kwargs):
        super().__init__(role=AgentRole.RESEARCHER, **kwargs)
    
    async def research(self, topic: str, depth: str = "comprehensive") -> str:
        """
        Perform research on a topic.
        
        Args:
            topic: Topic to research
            depth: Depth of research (quick, standard, comprehensive)
        
        Returns:
            Research results
        """
        prompt = self.get_prompt(f"Research: {topic}", f"Depth: {depth}")
        return await self.run_task(prompt)
    
    async def analyze(self, data: Any, analysis_type: str = "general") -> str:
        """
        Analyze data.
        
        Args:
            data: Data to analyze
            analysis_type: Type of analysis
        
        Returns:
            Analysis results
        """
        prompt = self.get_prompt(f"Analyze: {analysis_type}\nData: {str(data)[:1000]}")
        return await self.run_task(prompt)


class Coder(SpecializedAgent):
    """
    Coder Agent - Specialized in software development.
    """
    
    def __init__(self, **kwargs):
        super().__init__(role=AgentRole.CODER, **kwargs)
    
    async def write_code(self, requirements: str, language: str = "python") -> str:
        """
        Write code based on requirements.
        
        Args:
            requirements: What the code should do
            language: Programming language
        
        Returns:
            Generated code
        """
        prompt = self.get_prompt(f"Write {language} code for: {requirements}")
        return await self.run_task(prompt)
    
    async def debug(self, code: str, error: str = None) -> str:
        """
        Debug code.
        
        Args:
            code: Code to debug
            error: Error message if any
        
        Returns:
            Debug analysis and fixes
        """
        prompt = self.get_prompt(f"Debug code:\n{code}\nError: {error}")
        return await self.run_task(prompt)
    
    async def refactor(self, code: str, goals: list[str] = None) -> str:
        """
        Refactor code.
        
        Args:
            code: Code to refactor
            goals: Refactoring goals
        
        Returns:
            Refactored code
        """
        goals_str = ", ".join(goals) if goals else "improve readability, performance"
        prompt = self.get_prompt(f"Refactor code:\n{code}\nGoals: {goals_str}")
        return await self.run_task(prompt)


class Verifier(SpecializedAgent):
    """
    Verifier Agent - Specialized in verification and validation.
    """
    
    def __init__(self, **kwargs):
        super().__init__(role=AgentRole.VERIFIER, **kwargs)
    
    async def verify_code(self, code: str, requirements: str) -> str:
        """
        Verify code meets requirements.
        
        Args:
            code: Code to verify
            requirements: Requirements to check against
        
        Returns:
            Verification results
        """
        prompt = self.get_prompt(
            f"Verify code:\n{code}\n\nRequirements:\n{requirements}",
            "Verify that all requirements are met"
        )
        return await self.run_task(prompt)
    
    async def verify_facts(self, claims: list[str]) -> str:
        """
        Verify factual claims.
        
        Args:
            claims: List of claims to verify
        
        Returns:
            Verification results for each claim
        """
        claims_str = "\n".join([f"{i+1}. {c}" for i, c in enumerate(claims)])
        prompt = self.get_prompt(f"Verify these facts:\n{claims_str}")
        return await self.run_task(prompt)


class Chair(SpecializedAgent):
    """
    Chair Agent - Orchestrates multi-agent collaboration.
    """
    
    def __init__(self, **kwargs):
        super().__init__(role=AgentRole.CHAIR, **kwargs)
    
    async def coordinate(self, task: str, agents: list[Agent]) -> str:
        """
        Coordinate multiple agents to complete a task.
        
        Args:
            task: The task to complete
            agents: List of agents to coordinate
        
        Returns:
            Coordination plan and results
        """
        agents_str = ", ".join([a.config.name for a in agents])
        prompt = self.get_prompt(task, f"Available agents: {agents_str}")
        return await self.run_task(prompt)
    
    async def build_consensus(self, topic: str, agents: list[Agent]) -> str:
        """
        Build consensus among agents on a topic.
        
        Args:
            topic: Topic to discuss
            agents: Agents to include in discussion
        
        Returns:
            Consensus result
        """
        # TODO: Implement actual consensus building
        return await self.coordinate(f"Build consensus on: {topic}", agents)


class Critic(SpecializedAgent):
    """
    Critic Agent - Provides critical analysis.
    """
    
    def __init__(self, **kwargs):
        super().__init__(role=AgentRole.CRITIC, **kwargs)
    
    async def critique(self, work: str, criteria: list[str] = None) -> str:
        """
        Critique work based on criteria.
        
        Args:
            work: Work to critique
            criteria: Criteria to evaluate against
        
        Returns:
            Critique and improvement suggestions
        """
        criteria_str = ", ".join(criteria) if criteria else "quality, accuracy, completeness"
        prompt = self.get_prompt(work, f"Criteria: {criteria_str}")
        return await self.run_task(prompt)


class Synthesizer(SpecializedAgent):
    """
    Synthesizer Agent - Combines information from multiple sources.
    """
    
    def __init__(self, **kwargs):
        super().__init__(role=AgentRole.SYNTHESIZER, **kwargs)
    
    async def synthesize(self, sources: list[str], topic: str = None) -> str:
        """
        Synthesize information from multiple sources.
        
        Args:
            sources: List of information sources
            topic: Optional topic for context
        
        Returns:
            Synthesized result
        """
        sources_str = "\n\n".join([f"Source {i+1}:\n{s}" for i, s in enumerate(sources)])
        prompt = self.get_prompt(f"Synthesize:\n{sources_str}", f"Topic: {topic}")
        return await self.run_task(prompt)


class ToolRunner(SpecializedAgent):
    """
    Tool Runner Agent - Executes tools and commands.
    """
    
    def __init__(self, **kwargs):
        super().__init__(role=AgentRole.TOOL_RUNNER, **kwargs)
        self._allowed_tools = set()
    
    def add_tool(self, tool_name: str) -> None:
        """Add an allowed tool."""
        self._allowed_tools.add(tool_name)
    
    def remove_tool(self, tool_name: str) -> None:
        """Remove an allowed tool."""
        self._allowed_tools.discard(tool_name)
    
    async def execute_tool(self, tool_name: str, args: dict = None) -> str:
        """
        Execute a tool.
        
        Args:
            tool_name: Name of the tool to execute
            args: Arguments for the tool
        
        Returns:
            Tool execution result
        """
        if tool_name not in self._allowed_tools:
            return f"Error: Tool {tool_name} not allowed"
        
        # TODO: Actually execute the tool
        prompt = f"Execute tool: {tool_name}\nArgs: {json.dumps(args or {}, indent=2)}"
        return await self.run_task(prompt)


# Factory functions for creating specialized agents
async def create_researcher(name: str = None, **kwargs) -> Researcher:
    """Create a Researcher agent."""
    return Researcher(name=name or f"Researcher-{uuid.uuid4()[:8]}", **kwargs)


async def create_coder(name: str = None, **kwargs) -> Coder:
    """Create a Coder agent."""
    return Coder(name=name or f"Coder-{uuid.uuid4()[:8]}", **kwargs)


async def create_verifier(name: str = None, **kwargs) -> Verifier:
    """Create a Verifier agent."""
    return Verifier(name=name or f"Verifier-{uuid.uuid4()[:8]}", **kwargs)


async def create_chair(name: str = None, **kwargs) -> Chair:
    """Create a Chair agent."""
    return Chair(name=name or f"Chair-{uuid.uuid4()[:8]}", **kwargs)


async def create_critic(name: str = None, **kwargs) -> Critic:
    """Create a Critic agent."""
    return Critic(name=name or f"Critic-{uuid.uuid4()[:8]}", **kwargs)


async def create_synthesizer(name: str = None, **kwargs) -> Synthesizer:
    """Create a Synthesizer agent."""
    return Synthesizer(name=name or f"Synthesizer-{uuid.uuid4()[:8]}", **kwargs)


async def create_tool_runner(name: str = None, **kwargs) -> ToolRunner:
    """Create a Tool Runner agent."""
    return ToolRunner(name=name or f"ToolRunner-{uuid.uuid4()[:8]}", **kwargs)


# Role to class mapping
ROLE_TO_CLASS = {
    AgentRole.RESEARCHER: Researcher,
    AgentRole.CODER: Coder,
    AgentRole.VERIFIER: Verifier,
    AgentRole.CHAIR: Chair,
    AgentRole.CRITIC: Critic,
    AgentRole.SYNTHESIZER: Synthesizer,
    AgentRole.TOOL_RUNNER: ToolRunner,
}


async def create_by_role(role: AgentRole, name: str = None, **kwargs) -> SpecializedAgent:
    """
    Create an agent by its role.
    
    Args:
        role: The agent's role
        name: Optional name
        **kwargs: Additional configuration
    
    Returns:
        A specialized agent instance
    """
    cls = ROLE_TO_CLASS.get(role, SpecializedAgent)
    return cls(role=role, name=name, **kwargs)
