export const environment = {
  production: false,
  // Use proxy path instead of direct URL or in coder use "http://<workspace-name>.coder:<port>" with the port of forward of the api service
  apiUrl: "http://teams-api.localhost:8080",
  keycloak: {
    url: "http://platform-auth.localhost:8080",
    realm: "teams",
    clientId: "teams-ui",
  },
};
