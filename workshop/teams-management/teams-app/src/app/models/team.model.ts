// src/app/models/team.model.ts
export interface Team {
  id: string;
  name: string;
  created_at: string;
}

export interface TeamCreate {
  name: string;
}

export interface EventLink {
  label: string;
  url: string;
}

export interface Event {
  id: string;
  team_id: string;
  event_type: string;
  severity: 'info' | 'warning' | 'error';
  resource: string;
  namespace: string;
  message: string;
  timestamp: string;
  links: EventLink[];
  count: number;
}

export interface TeamDeployment {
  workload_type?: 'deployment' | 'rollout';
  name: string;
  namespace: string;
  replicas: number;
  available_replicas: number;
  updated_replicas: number;
  stable_replicas?: number;
  rollout_phase?: string;
  rollout_step?: string;
  created_at?: string;
}

export interface PolicyViolation {
  constraint: string;
  kind: string;
  namespace: string;
  resource: string;
  message: string;
}

export interface TeamPolicyStatus {
  in_violation: boolean;
  checked_at: string;
  violations: PolicyViolation[];
}
