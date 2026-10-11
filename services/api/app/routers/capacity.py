"""Capacity planning "what-if" simulator endpoints (roadmap 4.12).

Read-only: a simulation never writes anything and never touches OpenStack --
it re-uses the forecast trajectories (services/forecast_service.py) and the
latest FinOps cloud sample (the same capacity rows the Quotas & Budget
overview shows). That is why the routes only need the app-wide
authenticated-user dependency (main.py) and no extra role: asking "what if"
is not an action.

    GET  /api/v1/capacity/options   defaults, bounds, flavor + scenario presets
    POST /api/v1/capacity/simulate  run one scenario, get baseline vs scenario

The bounds live here (not in the frontend) so the UI sliders and the API's
validation can never disagree; the UI reads them from /options.
"""
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import capacity_simulator as sim

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/capacity", tags=["capacity"])


class ScenarioIn(BaseModel):
    """Every field is optional and defaults to "no change", so a request can
    move as many or as few levers as it likes -- `{}` is the baseline."""

    extra_vms: int = Field(0, ge=0, le=500, description="VMs added by the scenario")
    vm_vcpus: int = Field(2, ge=1, le=64)
    vm_ram_gb: float = Field(4.0, gt=0, le=512)
    vm_disk_gb: float = Field(40.0, ge=0, le=4096)
    vm_cpu_util_pct: float = Field(30.0, ge=0, le=100, description="Average CPU use of each new VM")
    vm_mem_util_pct: float = Field(70.0, ge=0, le=100, description="Average memory use of each new VM")
    vm_disk_fill_pct: float = Field(100.0, ge=0, le=100, description="Share of each volume actually consumed (thin provisioning < 100)")
    start_day: float = Field(0.0, ge=0, le=90, description="Days from now until the VMs start arriving")
    ramp_days: float = Field(0.0, ge=0, le=90, description="Days over which they arrive (0 = all at once)")
    workload_growth_pct_per_month: float = Field(0.0, ge=-50, le=200, description="Organic growth of existing load, compounding monthly")
    compute_nodes_added: int = Field(0, ge=0, le=20)
    storage_gb_added: float = Field(0.0, ge=0, le=1_000_000)
    nodes_added_day: float = Field(0.0, ge=0, le=90, description="Days from now until the added hardware is in service")
    threshold_pct: float = Field(sim.DEFAULT_THRESHOLD_PCT, gt=0, le=100, description="Utilisation counted as 'full'")
    horizon_days: int = Field(30, ge=1, le=90)

    def to_scenario(self) -> sim.Scenario:
        return sim.Scenario(**self.model_dump())


# Common sizes, so the UI doesn't hard-code them. Plain presets of the form
# the person can still edit field by field -- not Nova's real flavor list
# (reading that needs an OpenStack round-trip a what-if shouldn't pay).
FLAVOR_PRESETS = [
    {"name": "Tiny", "vm_vcpus": 1, "vm_ram_gb": 2, "vm_disk_gb": 20},
    {"name": "Small", "vm_vcpus": 2, "vm_ram_gb": 4, "vm_disk_gb": 40},
    {"name": "Medium", "vm_vcpus": 4, "vm_ram_gb": 8, "vm_disk_gb": 80},
    {"name": "Large", "vm_vcpus": 8, "vm_ram_gb": 16, "vm_disk_gb": 160},
]

# One-click starting points. Each deliberately sets two or more levers, so
# the first thing the person sees is a combined scenario they can then tweak.
SCENARIO_PRESETS = [
    {
        "id": "plus_20_vms",
        "label": "+20 VMs",
        "description": "Twenty small VMs, all at once.",
        "params": {"extra_vms": 20, "vm_vcpus": 2, "vm_ram_gb": 4, "vm_disk_gb": 40},
    },
    {
        "id": "new_project",
        "label": "New project",
        "description": "Eight medium VMs, starting next week and rolling in over two weeks.",
        "params": {"extra_vms": 8, "vm_vcpus": 4, "vm_ram_gb": 8, "vm_disk_gb": 80, "start_day": 7, "ramp_days": 14},
    },
    {
        "id": "plus_20_and_node",
        "label": "+20 VMs, +1 compute node",
        "description": "Twenty small VMs now; a hypervisor joins in two weeks.",
        "params": {"extra_vms": 20, "compute_nodes_added": 1, "nodes_added_day": 14},
    },
    {
        "id": "growth",
        "label": "Growth + new VMs",
        "description": "Ten VMs and 15% monthly organic growth.",
        "params": {"extra_vms": 10, "workload_growth_pct_per_month": 15},
    },
]


@router.get("/options")
def capacity_options():
    """What the simulator UI needs to build its controls: the default for
    every lever, the validated bounds, and the presets."""
    schema = ScenarioIn.model_json_schema()["properties"]
    bounds = {
        name: {
            "min": prop.get("minimum", prop.get("exclusiveMinimum")),
            "max": prop.get("maximum"),
            "default": prop.get("default"),
            "description": prop.get("description"),
        }
        for name, prop in schema.items()
    }
    return {
        "parameters": bounds,
        "flavor_presets": FLAVOR_PRESETS,
        "scenario_presets": SCENARIO_PRESETS,
        "horizons_days": [7, 30, 90],
        "resources": [{"metric": m, "label": v["label"], "node_role": v["role"]} for m, v in sim.RESOURCES.items()],
    }


@router.post("/simulate")
def simulate_scenario(body: ScenarioIn, db: Session = Depends(get_db)):
    """Baseline vs scenario capacity timeline for CPU, memory and disk, the
    projected threshold-crossing date in each case, the placement headroom
    (sellable capacity vs reserved) and the assumptions used. See
    services/capacity_simulator.py's docstring for the model."""
    return sim.run_scenario(db, body.to_scenario())
