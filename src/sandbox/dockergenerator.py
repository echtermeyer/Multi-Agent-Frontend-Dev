from io import BytesIO
import tarfile
import docker
import os
import re
import tempfile
from typing import Any, Set, Tuple
import logging

def extract_port_from_string(script: str) -> str:
    """
    Extracts the port number from a script string.

    Args:
        script (str): The Python script as a string.

    Returns:
        str: The port number as a string, defaults to '8000' if not found.
    """
    for line in script.splitlines():
        match = re.search(r'port=(\d+)', line)
        if match:
            return match.group(1)
    return '8000'

def extract_dependencies_from_string(script: str) -> Set[str]:
    """
    Extracts Python module dependencies from a script string.

    Args:
        script (str): The Python script as a string.

    Returns:
        Set[str]: A set of Python module dependencies.
    """
    dependencies = set()
    for line in script.splitlines():
        matches = re.findall(r'^import (\w+)|^from (\w+)', line)
        for match in matches:
            dependencies.add(match[0] or match[1])
    return dependencies

def execute_code(input_data: str, 
                 image_tag: str,
                 container_name: str,
                 dockerfile_method: callable, 
                 dependencies: Set[str],
                 port: str) -> Any:
    """
    Executes a script in a Docker container.

    Args:
        input_data (str): The script as a string or a path to the script file.

    Returns:
        Any: The Docker container object or an error message.
    """
    try:
        workspace_folder, script_name, script_string = prepare_script_workspace(input_data)
        client = docker.from_env()

        if not port:
            port = extract_port_from_string(script_string)
        if not dependencies:
            dependencies = extract_dependencies_from_string(script_string)


        dockerfile_bytes = dockerfile_method(script_name, dependencies, port)

        logging.info(f"Parsing file: {script_name}.")

        # Remove existing container if exists
        remove_existing_container_and_image(client, container_name, image_tag)

        # Build and run the Docker image
        container = build_and_run_container(client=client,
                                            workspace_folder=workspace_folder,
                                            dockerfile_bytes=dockerfile_bytes, 
                                            container_name=container_name,
                                            image_tag=image_tag,
                                            port=port)
        
        logging.info(f"Container {container.name} started successfully.")

        return container

    except Exception as e:
        logging.error(f"Error in execute_code: {str(e)}")
        return f"Error: {str(e)}"


def prepare_script_workspace(input_data: str) -> Tuple[str, str, str]:
    """
    Prepares the workspace for the Python script.

    Args:
        input_data (str): The Python script as a string or a path to the script file.

    Returns:
        Tuple[str, str, str]: A tuple containing the workspace folder path, script name, and script string.
    """
    script_name = ""

    if os.path.isfile(input_data):
        workspace_folder = os.path.dirname(input_data)
        script_name = os.path.basename(input_data)
        script_string = read_file(input_data)
    else:
        workspace_folder = tempfile.mkdtemp()
        script_string = input_data
        write_file(os.path.join(workspace_folder, script_name), script_string)

    logging.info(f"Preparing '{script_name}' in workspace '{workspace_folder}'")

    return workspace_folder, script_name, script_string

def read_file(file_path: str) -> str:
    """
    Reads and returns the content of a file.

    Args:
        file_path (str): The path to the file.

    Returns:
        str: The content of the file.
    """
    with open(file_path, 'r') as file:
        return file.read()

def write_file(file_path: str, content: str) -> None:
    """
    Writes content to a file.

    Args:
        file_path (str): The path to the file.
        content (str): The content to be written.
    """
    with open(file_path, 'w') as file:
        file.write(content)

def remove_existing_container_and_image(client: docker.DockerClient, container_name: str, image_tag: str) -> None:
    """
    Removes an existing Docker container by its name and the associated image by its tag.

    Args:
        client (docker.DockerClient): The Docker client instance.
        container_name (str): The name of the container to remove.
        image_tag (str): The tag of the image to remove.
    """
    try:
        # Try to get the container by name and remove it
        try:
            container = client.containers.get(container_name)
            logging.info(f"Removing container: {container_name}")
            container.stop()
            container.remove()
        except docker.errors.NotFound:
            logging.info(f"No container found with name: {container_name}")

        # Remove the image associated with the given tag
        try:
            image = client.images.get(image_tag)
            logging.info(f"Removing image: {image_tag}")
            client.images.remove(image.id)
        except docker.errors.ImageNotFound:
            logging.info(f"No image found with tag: {image_tag}")
        except docker.errors.APIError as e:
            logging.error(f"Error removing image with tag {image_tag}: {str(e)}")

    except Exception as e:
        logging.error(f"General error in removing container or image: {str(e)}")

def create_tar_archive(source_path):
    """
    Creates a tar archive of the given source path.

    Args:
        source_path (str): The path to create a tar archive of.

    Returns:
        BytesIO: The tar archive as a BytesIO object.
    """
    pw_tarstream = BytesIO()
    with tarfile.open(fileobj=pw_tarstream, mode='w') as tar:
        # Add files in the directory to the tarfile
        for filename in os.listdir(source_path):
            filepath = os.path.join(source_path, filename)
            tar.add(filepath, arcname=filename)
    pw_tarstream.seek(0)
    return pw_tarstream

def copy_files_to_container(container, tar_stream, destination_path):
    """
    Copies files from a tar archive to a container.

    Args:
        container (docker.models.containers.Container): The container to copy files to.
        tar_stream (BytesIO): The tar archive stream.
        destination_path (str): The path in the container to extract files to.
    """
    container.put_archive(destination_path, tar_stream)

def build_and_run_container(client: docker.DockerClient, 
                            workspace_folder: str, 
                            dockerfile_bytes: BytesIO, 
                            image_tag: str, 
                            port: str, 
                            network_name: str = "Agentcy", 
                            container_name: str = None) -> docker.models.containers.Container:
    """
    Builds and runs a Docker container on a specified network, copies files from the workspace folder, and restarts the container.

    Args:
        client (docker.DockerClient): The Docker client instance.
        workspace_folder (str): The workspace folder path.
        dockerfile_bytes (BytesIO): The Dockerfile as a BytesIO object.
        image_tag (str): The tag for the Docker image.
        port (str): The port number to expose.
        network_name (str): The name of the Docker network to use.
        container_name (str, optional): The name of the Docker container. Defaults to None.

    Returns:
        docker.models.containers.Container: The Docker container object.
    """
    # Check if the network exists, if not, create it
    networks = client.networks.list(names=[network_name])
    if not networks:
        client.networks.create(network_name, driver="bridge")

    # Build the image
    image, build_logs = client.images.build(
        path=workspace_folder,
        fileobj=dockerfile_bytes,
        rm=True,
        tag=image_tag,
        quiet=False
    )

    # Run the container
    container = client.containers.run(
        image.id,
        ports={f"{port}/tcp": int(port)},
        network=network_name,
        name=container_name,
        stderr=True,
        stdout=True,
        detach=True,
    )

    # Create a tar archive of the specific folder
    tar_stream = create_tar_archive(workspace_folder)
    
    # Copy files to the container
    copy_files_to_container(container, tar_stream, '/usr/share/nginx/html/')

    # Restart the container
    container.restart()

    return container
