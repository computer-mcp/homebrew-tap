class AppleCli < Formula
  desc "Automate Apple apps using CLI and MCP"
  homepage "https://github.com/computer-mcp/apple-cli"
  url "https://github.com/computer-mcp/apple-cli/releases/download/v0.1.0-alpha.4/apple-cli-0.1.0-alpha.4-macos-arm64.tar.gz"
  sha256 "12b0550c655af2dd30699a89a032d9a9b7b134fa6a7938844fd333a413c49f4f"
  license "Apache-2.0"

  depends_on arch: :arm64
  depends_on macos: :golden_gate

  # Preserve the accepted executables and their bundled runtime signatures.
  skip_clean "libexec"

  def install
    libexec.install Dir["*"]
    bin.install_symlink libexec/"bin/apple", libexec/"bin/apple-cli-mcp"
  end

  test do
    assert_equal version.to_s, shell_output("#{bin}/apple --version").strip
    assert_equal version.to_s, shell_output("#{bin}/apple-cli-mcp --version").strip
    assert_match "notes", shell_output("#{bin}/apple --help")
    preview = JSON.parse(shell_output("#{bin}/apple notifications preview --title Homebrew --body test --json"))
    assert_equal true, preview["ok"]
    assert_equal false, preview.dig("data", "externalAction")
  end
end
