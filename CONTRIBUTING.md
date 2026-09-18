# 🤝 Contributing to HERMUS Agent Free

Thank you for your interest in contributing to HERMUS Agent Free! We welcome contributions from everyone.

## 📋 Table of Contents

- [🎯 Getting Started](#-getting-started)
- [🐛 Reporting Bugs](#-reporting-bugs)
- [🚀 Suggesting Features](#-suggesting-features)
- [💻 Development Setup](#-development-setup)
- [📝 Commit Message Guidelines](#-commit-message-guidelines)
- [🎨 Code Style Guidelines](#-code-style-guidelines)
- [🧪 Testing](#-testing)
- [📚 Documentation](#-documentation)
- [🤝 Pull Request Process](#-pull-request-process)
- [👥 Community Guidelines](#-community-guidelines)

## 🎯 Getting Started

Before you begin:

1. **Star** the repository ⭐
2. **Fork** the repository to your GitHub account
3. **Clone** your fork locally
4. **Read** this contributing guide

## 🐛 Reporting Bugs

### Before Submitting a Bug Report

1. **Check the existing issues** to see if the bug has already been reported
2. **Verify the bug** on the latest version of HERMUS
3. **Gather information** about your environment:
   - Operating System
   - Python version
   - HERMUS version
   - Browser (if web-related)

### How to Submit a Bug Report

Use the [Bug Report template](.github/ISSUE_TEMPLATE/bug_report.md) and include:

- A clear, descriptive title
- Steps to reproduce the issue
- Expected vs actual behavior
- Screenshots or error logs (if applicable)
- Your environment details

## 🚀 Suggesting Features

### Before Submitting a Feature Request

1. **Check existing issues** to see if the feature has already been requested
2. **Consider if the feature** aligns with HERMUS's goals and architecture
3. **Think about alternatives** - could this be implemented as a plugin or skill?

### How to Submit a Feature Request

Use the [Feature Request template](.github/ISSUE_TEMPLATE/feature_request.md) and include:

- A clear, descriptive title
- The problem you're trying to solve
- Your proposed solution
- Any alternatives you've considered
- Additional context or mockups

## 💻 Development Setup

### Prerequisites

- Python 3.10 or higher
- Git
- pip (Python package manager)

### Setup Steps

```bash
# Clone your fork
git clone https://github.com/your-username/hermus-agent-free.git
cd hermus-agent-free

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt -r requirements-dev.txt

# Install in development mode
pip install -e .

# Run the bootstrap script
./setup.sh

# Verify installation
hermus --help
```

### Running Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_file.py

# Run with coverage
pytest --cov=core --cov-report=html
```

## 📝 Commit Message Guidelines

We follow conventional commit messages for consistency. Each commit message should:

1. **Start with a type**: `feat:`, `fix:`, `docs:`, `style:`, `refactor:`, `test:`, `chore:`
2. **Use the imperative mood**: "Add feature" not "Added feature"
3. **Be concise but descriptive**: 50-72 characters for the subject line
4. **Reference issues**: Include issue numbers if applicable

### Examples

```
feat: add support for new model provider
fix: resolve memory leak in mission engine
 docs: update README with installation instructions
refactor: simplify tool execution logic
test: add unit tests for counsel system
chore: update dependencies
```

## 🎨 Code Style Guidelines

### Python Code

- Follow **PEP 8** style guide
- Use **4 spaces** for indentation
- Maximum line length: **130 characters** (as per project config)
- Use **snake_case** for variable and function names
- Use **PascalCase** for class names
- Use **UPPER_CASE** for constants
- Include **type hints** where appropriate

### JavaScript Code

- Use **camelCase** for variable and function names
- Use **PascalCase** for class names
- Use **2 spaces** for indentation
- Follow **ESLint** recommendations

### Formatting

```bash
# Format Python code
ruff format

# Lint Python code
ruff check

# Type check
mypy core/
```

## 🧪 Testing

### Test Structure

- Place tests in the `tests/` directory
- Use **pytest** as the test framework
- Name test files with `test_` prefix
- Group related tests in classes

### Test Examples

```python
# tests/test_module.py
import pytest
from hermus.core.module import some_function


class TestModule:
    def test_function_behavior(self):
        result = some_function(input)
        assert result == expected_output

    def test_error_handling(self):
        with pytest.raises(ValueError):
            some_function(invalid_input)
```

### Running Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test
pytest tests/test_module.py::TestModule::test_function_behavior

# Run with coverage
pytest --cov=core --cov-report=html
```

## 📚 Documentation

### Documentation Standards

- Use **Markdown** for all documentation
- Follow **Google-style** docstrings for Python code
- Keep documentation **up-to-date** with code changes
- Include **examples** where helpful

### Docstring Example

```python
"""
Summary line.

Extended description. This can span multiple paragraphs.

Args:
    param1 (type): Description of param1.
    param2 (type): Description of param2.

Returns:
    type: Description of return value.

Raises:
    ValueError: If something is invalid.
"""
```

### Documentation Files

- **README.md**: Main project overview
- **QUICKSTART.md**: Installation and quick start guide
- **ARCHITECTURE.md**: Technical architecture documentation
- **LIVING_CONTROL_ROOM.md**: Control room features and usage
- **docs/**: Additional technical documentation

## 🤝 Pull Request Process

### Before Submitting

1. **Fork** the repository
2. **Create a feature branch**: `git checkout -b feature/your-feature`
3. **Make your changes** following the code style guidelines
4. **Write tests** for new functionality
5. **Update documentation** as needed
6. **Run tests**: Ensure all tests pass
7. **Run linting**: `ruff check` and `mypy`

### Submitting a Pull Request

1. **Push** your changes to your fork
2. **Open a Pull Request** to the main repository
3. **Use the PR template** and fill out all sections
4. **Reference any related issues** (use `Closes #123` to auto-close)
5. **Wait for review** and address any feedback

### PR Review Process

1. **Initial Review**: Maintainers will review your PR within 3-5 business days
2. **Feedback**: You may receive requests for changes or clarifications
3. **CI Checks**: All tests must pass, and code must meet quality standards
4. **Approval**: Once approved, a maintainer will merge your PR
5. **Release**: Your changes will be included in the next release

## 👥 Community Guidelines

### Code of Conduct

By participating in this project, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md). Be respectful and inclusive in all interactions.

### Communication

- **GitHub Issues**: For bug reports and feature requests
- **GitHub Discussions**: For general questions and discussions
- **Discord**: For real-time chat (link in README)

### Recognition

All meaningful contributions will be recognized:
- **Code contributions** will be credited in the changelog
- **Bug reports** and **feature suggestions** will be acknowledged
- **Documentation improvements** are highly valued
- **Community help** (answering questions, etc.) is appreciated

## 🎁 Contribution Recognition

All contributors will be listed in:
- The **CONTRIBUTORS** section of the README
- The **GitHub contributors** graph
- Release **changelogs**

## 📜 License

By contributing to HERMUS Agent Free, you agree that your contributions will be licensed under the **MIT License**.

## 🙏 Thank You!

Your contributions help make HERMUS Agent Free better for everyone. We appreciate your time, effort, and expertise!

---

**Happy Coding!** ⚡
