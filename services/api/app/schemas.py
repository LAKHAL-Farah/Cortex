import uuid
from datetime import datetime
from enum import Enum
from ipaddress import ip_address, ip_network
from pydantic import BaseModel, Field, field_validator, ConfigDict
from ipaddress import ip_address, ip_network

MANAGED_SUBNETS = [
    ip_network("10.0.1.0/24"),   # controller, compute1, compute2
    ip_network("10.0.2.0/24"),   # storage
]

class NodeRole(str, Enum):
    controller = "controller"
    compute = "compute"
    storage = "storage"
    monitoring = "monitoring"


class NodeBase(BaseModel):
    hostname: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    ip_address: str
    role: NodeRole
    exporter_port: int = Field(default=9100, ge=1, le=65535)
    is_active: bool = True

    @field_validator("ip_address")
    @classmethod
    def ip_must_be_in_private_subnet(cls, v: str) -> str:
        addr = ip_address(v)
        if not any(addr in subnet for subnet in MANAGED_SUBNETS):
            allowed = ", ".join(str(s) for s in MANAGED_SUBNETS)
        raise ValueError(f"ip_address must be within one of: {allowed}")
        return v


class NodeCreate(NodeBase):
    pass


class NodeUpdate(NodeBase):
    pass





class NodeOut(NodeBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime




