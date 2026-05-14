using System.Net;
using Xunit;
using Microsoft.AspNetCore.Mvc.Testing;

namespace EmojiApi.Tests;

public class EmojiApiTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly HttpClient _client;

    public EmojiApiTests(WebApplicationFactory<Program> factory)
    {
        _client = factory.WithWebHostBuilder(builder =>
        {
            builder.UseSetting("EMOJI_COLOR", "green");
        }).CreateClient();
    }

    [Fact]
    public async Task Health_ReturnsOk()
    {
        var response = await _client.GetAsync("/health");
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    [Fact]
    public async Task Health_ReturnsHealthyStatus()
    {
        var response = await _client.GetAsync("/health");
        var body = await response.Content.ReadAsStringAsync();
        Assert.Contains("healthy", body);
    }

    [Fact]
    public async Task Random_ReturnsOk()
    {
        var response = await _client.GetAsync("/random");
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    [Fact]
    public async Task Random_ReturnsNonEmptyBody()
    {
        var response = await _client.GetAsync("/random");
        var body = await response.Content.ReadAsStringAsync();
        Assert.NotEmpty(body);
    }

    [Fact]
    public async Task All_ReturnsOk()
    {
        var response = await _client.GetAsync("/all");
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    [Fact]
    public async Task All_ReturnsColorAndEmojis()
    {
        var response = await _client.GetAsync("/all");
        var body = await response.Content.ReadAsStringAsync();
        Assert.Contains("color", body);
        Assert.Contains("emojis", body);
    }

    [Fact]
    public async Task All_ReturnsGreenEmojis()
    {
        var response = await _client.GetAsync("/all");
        var body = await response.Content.ReadAsStringAsync();
        Assert.Contains("green", body);
    }

    [Fact]
    public async Task Root_ReturnsHtml()
    {
        var response = await _client.GetAsync("/");
        var contentType = response.Content.Headers.ContentType?.MediaType;
        Assert.Equal("text/html", contentType);
    }
}
