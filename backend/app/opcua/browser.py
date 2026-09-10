from collections import deque

from asyncua import ua
from asyncua.client.client import Client
from asyncua.common.node import Node
from asyncua.ua.uaerrors import UaError

from app.core.config import settings
from app.models import OpcUaBrowseNodePublic
from app.opcua.security import validate_allowed_endpoint


async def browse_variable_nodes(
    *, endpoint_url: str, root_node_id: str, max_depth: int, max_nodes: int
) -> tuple[list[OpcUaBrowseNodePublic], bool]:
    endpoint = validate_allowed_endpoint(endpoint_url)
    rows: list[OpcUaBrowseNodePublic] = []
    visited: set[str] = set()
    queue: deque[tuple[Node, int]] = deque()

    async with Client(
        url=endpoint,
        timeout=settings.OPCUA_CLIENT_TIMEOUT_SECONDS,
        watchdog_intervall=1.0,
    ) as client:
        queue.append((client.get_node(root_node_id), 0))
        while queue and len(visited) < max_nodes:
            node, depth = queue.popleft()
            node_id = node.nodeid.to_string()
            if node_id in visited:
                continue
            visited.add(node_id)

            # Do not recursively traverse the enormous standard namespace tree
            # below Objects. Industrial variables are expected in a vendor or
            # project namespace; callers can still provide a specific ns=0 root.
            if depth > 0 and node.nodeid.NamespaceIndex == 0:
                continue

            node_class = await node.read_node_class()
            if node_class == ua.NodeClass.Variable:
                browse_name = await node.read_browse_name()
                display_name = await node.read_display_name()
                try:
                    variant_type = await node.read_data_type_as_variant_type()
                    data_type = variant_type.name
                except UaError:
                    data_type = None
                rows.append(
                    OpcUaBrowseNodePublic(
                        node_id=node_id,
                        browse_name=browse_name.Name or node_id,
                        display_name=display_name.Text or browse_name.Name or node_id,
                        data_type=data_type,
                    )
                )

            if depth < max_depth:
                children = await node.get_children()
                queue.extend((child, depth + 1) for child in children)

    return rows, bool(queue)
