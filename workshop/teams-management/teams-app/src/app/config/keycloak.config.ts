import { KeycloakConfig } from 'keycloak-js';
import { environment } from '../../environments/environment';

const keycloakConfig: KeycloakConfig = environment.keycloak;

export default keycloakConfig;
