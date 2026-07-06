# Test stage — runs tests inside the SDK container; CMD outputs coverage % to stdout
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS test
WORKDIR /src
COPY EmojiApi/EmojiApi.csproj EmojiApi/
COPY EmojiApi.Tests/EmojiApi.Tests.csproj EmojiApi.Tests/
RUN dotnet restore EmojiApi.Tests/EmojiApi.Tests.csproj
COPY EmojiApi/ EmojiApi/
COPY EmojiApi.Tests/ EmojiApi.Tests/
RUN dotnet test EmojiApi.Tests/EmojiApi.Tests.csproj \
    --settings EmojiApi.Tests/coverlet.runsettings \
    --collect:"XPlat Code Coverage" \
    --results-directory /testresults \
    --no-restore \
    --verbosity quiet
# Print the overall line-rate as an integer percentage (e.g. "87")
CMD ["sh", "-c", "grep -m1 'line-rate=' /testresults/*/coverage.cobertura.xml | awk -F'\"' '{printf \"%d\\n\", $2 * 100}'"]

# Vulnerability check stage — CMD outputs dotnet list package JSON to stdout
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS vuln-check
WORKDIR /src
COPY EmojiApi/EmojiApi.csproj EmojiApi/
RUN dotnet restore EmojiApi/EmojiApi.csproj
CMD ["dotnet", "list", "EmojiApi/EmojiApi.csproj", "package", "--vulnerable", "--include-transitive", "--format", "json"]

# Build stage
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
COPY EmojiApi/EmojiApi.csproj EmojiApi/
RUN dotnet restore EmojiApi/EmojiApi.csproj
COPY EmojiApi/ EmojiApi/
RUN dotnet publish EmojiApi/EmojiApi.csproj -c Release -o /app/publish --no-restore

# Runtime stage — chiseled: minimal Ubuntu, non-root by default, no shell
FROM mcr.microsoft.com/dotnet/aspnet:10.0-noble-chiseled AS runtime
WORKDIR /app

COPY --from=build /app/publish .

# Color variant — baked in at build time, can still be overridden at runtime
ARG COLOR=green
ENV EMOJI_COLOR=${COLOR}

ENV ASPNETCORE_URLS=http://+:8080
EXPOSE 8080

ENTRYPOINT ["dotnet", "EmojiApi.dll"]
