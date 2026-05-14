using Scalar.AspNetCore;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddOpenApi();

var app = builder.Build();

app.MapOpenApi();
app.MapScalarApiReference(options =>
{
    options.Title = "Emoji API";
});

var color = (Environment.GetEnvironmentVariable("EMOJI_COLOR") ?? "green").ToLowerInvariant();

var emojisByColor = new Dictionary<string, string[]>
{
    ["purple"] = ["💜", "🟣", "🪻", "🔮", "🍇", "🫐", "☂️", "🟪"],
    ["green"]  = ["💚", "🟢", "🌿", "🍀", "🥑", "🐸", "🌱", "🦚"],
    ["orange"] = ["🧡", "🟠", "🎃", "🦊", "🍊", "🔥", "🔶", "🦁"],
    ["red"]    = ["❤️", "🔴", "🍎", "🌹", "♥️", "⛔️", "🚨", "🎈"],
};

if (!emojisByColor.ContainsKey(color))
{
    app.Logger.LogWarning("Unknown EMOJI_COLOR '{Color}', falling back to green", color);
    color = "green";
}

app.MapGet("/", () =>
{
    var displayColor = color switch
    {
        "purple" => "#a855f7",
        "green"  => "#22c55e",
        "orange" => "#f97316",
        _        => "#ffffff",
    };
    var html = $$"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="UTF-8"/>
          <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
          <title>Emoji API</title>
          <style>
            *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
            body {
              background: #000;
              display: flex;
              align-items: center;
              justify-content: center;
              min-height: 100vh;
              font-family: 'Arial Black', 'Impact', sans-serif;
              overflow: hidden;
            }
            h1 {
              color: {{displayColor}};
              font-size: clamp(2rem, 8vw, 6rem);
              font-weight: 900;
              text-align: center;
              text-shadow: 0 0 40px {{displayColor}}88;
              letter-spacing: -0.02em;
              padding: 1rem;
              position: relative;
              z-index: 1;
            }
            .emoji-particle {
              position: fixed;
              font-size: clamp(1.5rem, 4vw, 3rem);
              pointer-events: none;
              user-select: none;
              animation: fadeIn 0.3s ease-out forwards;
            }
            @keyframes fadeIn {
              from { opacity: 0; transform: scale(0.5); }
              to   { opacity: 1; transform: scale(1); }
            }
          </style>
        </head>
        <body>
          <h1>You get {{char.ToUpper(color[0]) + color[1..]}} emojis!</h1>
          <script>
            const max = 100;
            let count = 0;
            const interval = setInterval(async () => {
              if (count >= max) { clearInterval(interval); return; }
              const emoji = await fetch('/random').then(r => r.text()).catch(() => null);
              if (!emoji) return;
              const el = document.createElement('span');
              el.className = 'emoji-particle';
              el.textContent = emoji;
              el.style.left = Math.random() * 100 + 'vw';
              el.style.top  = Math.random() * 100 + 'vh';
              document.body.appendChild(el);
              count++;
            }, 1000);
          </script>
        </body>
        </html>
        """;
    return Results.Content(html, "text/html");
})
.ExcludeFromDescription();

app.MapGet("/random", () =>
{
    var emojis = emojisByColor[color];
    var emoji = emojis[Random.Shared.Next(emojis.Length)];
    return Results.Content(emoji, "text/plain; charset=utf-8");
})
.WithName("GetRandomEmoji")
.WithSummary("Get a random emoji")
.WithDescription("Returns a random emoji in the color configured via the EMOJI_COLOR environment variable.");

app.MapGet("/all", () =>
{
    var emojis = emojisByColor[color];
    return Results.Ok(new { color, emojis });
})
.WithName("GetAllEmojis")
.WithSummary("Get all emojis")
.WithDescription("Returns all available emojis for the current color.");

app.MapGet("/health", () => Results.Ok(new { status = "healthy", color }))
.WithName("Health")
.WithSummary("Health check")
.ExcludeFromDescription();

app.Run();

// Needed for WebApplicationFactory<Program> in integration tests
public partial class Program { }
