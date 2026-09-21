# KOMPAS-3D AI Bridge

AI engineering bridge for KOMPAS-3D using Python, COM/API7 and MCP.

The project connects AI agents and coding assistants to KOMPAS-3D through a deterministic execution layer.

## Architecture

AI / LLM / Coding Agent
        |
        v
       MCP
        |
        v
Python deterministic bridge
        |
        v
KOMPAS COM / API7
        |
        v
KOMPAS-3D

## Features

- AI agent integration with KOMPAS-3D
- MCP server
- Python deterministic execution layer
- KOMPAS COM / API7 integration
- Parts and assemblies
- 3D document automation
- Engineering-oriented CAD operations
- Validation before and after execution
- Separation between probabilistic AI reasoning and deterministic CAD API calls

## Requirements

- Windows
- KOMPAS-3D
- Python 3
- pywin32

Install dependencies:

    pip install -r requirements.txt

## Project structure

    bootstrap/   Bridge startup scripts
    common/      Shared components
    config/      Example configuration
    jobs/        Job definitions
    mcp/         MCP server
    skills/      Agent skills
    src/         Bridge implementation
    tests/       Tests and self-checks

## Configuration

Copy:

    config/agent_config.example.json

to:

    config/agent_config.json

Then adjust paths for your local environment.

The real agent_config.json is excluded from Git.

## Safety

Customer CAD data and project-specific files are not included in this repository.

The repository excludes local CAD files, runtime logs, workspaces, local configuration and secrets.

## Version

Current public base: 1.1.14_CAP2

## Disclaimer

This is an independent engineering automation project.

KOMPAS-3D is a product of ASCON. This project is not an official ASCON product and is not affiliated with or endorsed by ASCON.
