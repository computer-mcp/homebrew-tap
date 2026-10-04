cask "computer-mcp" do
  version "1.3.2"
  sha256 "b60df082bf597bef2168353e42cc568f8606434822ca820acc6a80cb0f909e02"

  url "https://github.com/computer-mcp/computer-mcp/releases/download/v#{version}/Computer-MCP-#{version}-universal.dmg"
  name "Computer MCP"
  desc "Governed local MCP gateway for ChatGPT"
  homepage "https://github.com/computer-mcp/computer-mcp"

  depends_on macos: :sonoma

  app "Computer MCP.app"
end
