// src/environments/environment.prod.ts
export const environment = {
  production: true,
  apiUrl: 'http://teams-api.localhost:8080',
  keycloak: {
    url: 'http://platform-auth.localhost:8080',
    realm: 'teams',
    clientId: 'teams-ui',
  },
};
