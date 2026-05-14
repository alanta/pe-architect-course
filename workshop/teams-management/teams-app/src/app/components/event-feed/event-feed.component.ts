import { Component, Input, OnInit, OnDestroy } from '@angular/core';
import { Subscription, timer } from 'rxjs';
import { switchMap } from 'rxjs/operators';
import { Event } from '../../models/team.model';
import { TeamsService } from '../../services/teams.service';

@Component({
  selector: 'app-event-feed',
  templateUrl: './event-feed.component.html',
  styleUrls: ['./event-feed.component.css'],
})
export class EventFeedComponent implements OnInit, OnDestroy {
  @Input() teamId!: string;

  events: Event[] = [];
  private sub?: Subscription;

  constructor(private teamsService: TeamsService) {}

  ngOnInit() {
    // task 9.4: poll every 30s using timer + switchMap
    this.sub = timer(0, 30000)
      .pipe(switchMap(() => this.teamsService.getTeamEvents(this.teamId, 5)))
      .subscribe({
        next: (events) => (this.events = events),
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
}
