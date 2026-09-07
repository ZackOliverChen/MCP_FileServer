### MCP File Server

A simple file service program designed to stream files locally or remotely. It is also packed with a client operation tool <mcp_fs_client.py>.

#### Core Use Case
This program was originally built to copy files between an **OpenClaw** sandbox and the host environment. For safety reasons, OpenClaw is usually intentionally installed under a non-admin user account. 

While standard tools like `rsync` or `rcp` are typically used to transfer data to and from the OpenClaw user space, they could be inconvenient for daily workflows. This program was created to solve that specific problem by providing a more seamless transfer experience.

Compared to generic FTP tools, this program provides some specific features, such as protecting host modifications from being overwritten by remote clients, which usually are bots.

#### Key Features & Flexibility
* **Extensible:** The program can easily be extended to other use cases beyond its original sandbox design.
* **Cross-Machine Transfers:** You can use the built-in client tools to transfer files directly between two different computers.
