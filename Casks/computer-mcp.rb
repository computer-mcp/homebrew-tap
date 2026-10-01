cask "computer-mcp" do
  version "1.3.1"
  sha256 "ece18ea8d0734323af9f2ccad07897c25d0c464199d72ccbd54ed6d841d2ce6b"

  url "https://github.com/computer-mcp/computer-mcp/releases/download/v#{version}/Computer-MCP-#{version}-universal.dmg"
  name "Computer MCP"
  desc "Governed local MCP gateway for ChatGPT"
  homepage "https://github.com/computer-mcp/computer-mcp"

  depends_on macos: :sonoma

  app "Computer MCP.app"
end
