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
}
