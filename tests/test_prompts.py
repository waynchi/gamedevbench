from gamedevbench.src.utils.prompts import create_task_prompt


def test_task_prompt_ends_with_filesystem_and_documentation_scope():
    prompt = create_task_prompt(
        {"instruction": "Build the requested Godot scene."},
        use_runtime_video=True,
    )

    assert prompt.endswith(
        "Your current directory, /workspace, contains all task-specific files "
        "available to you. Do not search outside /workspace for other files as "
        "you will be blocked.\n"
        "You have restricted web search capabilities. You may find godot "
        "documentation for 4.4.1 at "
        "https://docs.godotengine.org/en/4.4/"
    )
