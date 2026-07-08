import { Component, Input, OnInit, OnDestroy } from '@angular/core';
import { forkJoin, Subscription, timer } from 'rxjs';
import { switchMap } from 'rxjs/operators';
import { Event, TeamDeployment, TeamPolicyStatus } from '../../models/team.model';
import { TeamsService } from '../../services/teams.service';

@Component({
  selector: 'app-event-feed',
  templateUrl: './event-feed.component.html',
  styleUrls: ['./event-feed.component.css'],
})
export class EventFeedComponent implements OnInit, OnDestroy {
  @Input() teamId!: string;

  events: Event[] = [];
  deployments: TeamDeployment[] = [];
  policyStatus?: TeamPolicyStatus;
  private sub?: Subscription;

  constructor(private teamsService: TeamsService) {}

  ngOnInit() {
    // task 9.4: poll every 30s using timer + switchMap
    this.sub = timer(0, 30000)
      .pipe(
        switchMap(() =>
          forkJoin({
            events: this.teamsService.getTeamEvents(this.teamId, 5),
            deployments: this.teamsService.getTeamDeployments(this.teamId),
            policyStatus: this.teamsService.getTeamPolicyStatus(this.teamId),
          })
        )
      )
      .subscribe({
        next: ({ events, deployments, policyStatus }) => {
          this.events = events;
          this.deployments = deployments;
          this.policyStatus = policyStatus;
        },
        error: (err) => console.error('Event feed error:', err),
      });
  }

  ngOnDestroy() {
    this.sub?.unsubscribe();
  }

  formatTime(timestamp: string): string {
    return new Date(timestamp).toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  }

  eventIcon(severity: string): string {
    if (severity === 'error') return '✕';
    if (severity === 'warning') return '⚠';
    return 'ℹ';
  }

  // For gatekeeper violations, strip the "Admission webhook ... Message: " prefix.
  // Falls back to the raw message for other event types.
  displayMessage(event: Event): string {
    const match = event.message.match(/,\s*Message:\s*(.+)$/s);
    return match ? match[1].trim() : event.message;
  }

  // Extract the webhook/source name from the admission webhook prefix, if present.
  displaySource(event: Event): string {
    const match = event.message.match(/Admission webhook "([^"]+)"/);
    return match ? match[1] : event.event_type;
  }

  deploymentReplicas(deployment: TeamDeployment): string {
    return `${deployment.available_replicas}/${deployment.replicas}`;
  }

  deploymentStatusLabel(deployment: TeamDeployment): string {
    if (deployment.workload_type === 'rollout' && deployment.rollout_phase) {
      return deployment.rollout_phase;
    }
    return 'running';
  }

  deploymentStatusClass(deployment: TeamDeployment): string {
    const phase = (deployment.rollout_phase || '').toLowerCase();
    if (phase === 'paused') return 'status-paused';
    if (phase === 'progressing') return 'status-progressing';
    if (phase === 'degraded' || phase === 'unhealthy') return 'status-degraded';
    if (phase === 'healthy') return 'status-healthy';
    return 'status-running';
  }

  rolloutPodsLabel(deployment: TeamDeployment): string {
    const stable = deployment.stable_replicas ?? Math.max(deployment.replicas - deployment.updated_replicas, 0);
    return `${deployment.updated_replicas}/${stable}`;
  }

  formatDeploymentTime(timestamp?: string): string {
    if (!timestamp) {
      return 'unknown';
    }
    return new Date(timestamp).toLocaleString('en-US', {
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  titleCase(value?: string): string {
    if (!value) {
      return 'Unknown';
    }
    return value.charAt(0).toUpperCase() + value.slice(1).toLowerCase();
  }

  policyStateLabel(): string {
    if (!this.policyStatus) {
      return 'unknown';
    }
    return this.policyStatus.in_violation ? 'in violation' : 'compliant';
  }
}
