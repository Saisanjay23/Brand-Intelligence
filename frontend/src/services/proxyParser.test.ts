import { describe, expect, it } from "vitest";
import { describeParsedProxy, parseProxyString } from "./proxyParser";

describe("parseProxyString", () => {
  it("parses a bare host:port, defaulting to http", () => {
    expect(parseProxyString("203.0.113.10:8080")).toEqual({
      server: "http://203.0.113.10:8080",
    });
  });

  it("parses host:port:username:password", () => {
    expect(parseProxyString("203.0.113.10:8080:myuser:mypass")).toEqual({
      server: "http://203.0.113.10:8080",
      username: "myuser",
      password: "mypass",
    });
  });

  it("parses host:port:username:password with no password (trailing empty field)", () => {
    expect(parseProxyString("203.0.113.10:8080:myuser:")).toEqual({
      server: "http://203.0.113.10:8080",
      username: "myuser",
    });
  });

  it("parses username:password@host:port", () => {
    expect(parseProxyString("myuser:mypass@203.0.113.10:8080")).toEqual({
      server: "http://203.0.113.10:8080",
      username: "myuser",
      password: "mypass",
    });
  });

  it("parses username@host:port with no password", () => {
    expect(parseProxyString("myuser@203.0.113.10:8080")).toEqual({
      server: "http://203.0.113.10:8080",
      username: "myuser",
    });
  });

  for (const scheme of ["http", "https", "socks4", "socks5", "socks5h"]) {
    it(`parses an explicit scheme://host:port (${scheme})`, () => {
      expect(parseProxyString(`${scheme}://203.0.113.10:1080`)).toEqual({
        server: `${scheme}://203.0.113.10:1080`,
      });
    });
  }

  it("parses scheme://username:password@host:port, preserving the scheme", () => {
    expect(parseProxyString("socks5://myuser:mypass@203.0.113.10:1080")).toEqual({
      server: "socks5://203.0.113.10:1080",
      username: "myuser",
      password: "mypass",
    });
  });

  it("parses scheme://username@host:port with no password", () => {
    expect(parseProxyString("http://myuser@203.0.113.10:8080")).toEqual({
      server: "http://203.0.113.10:8080",
      username: "myuser",
    });
  });

  it("decodes percent-encoded credentials", () => {
    expect(parseProxyString("http://user%40name:p%40ss@203.0.113.10:8080")).toEqual({
      server: "http://203.0.113.10:8080",
      username: "user@name",
      password: "p@ss",
    });
  });

  it("tolerates a trailing slash on the host:port", () => {
    expect(parseProxyString("http://203.0.113.10:8080/")).toEqual({
      server: "http://203.0.113.10:8080",
    });
  });

  it("trims surrounding whitespace", () => {
    expect(parseProxyString("   203.0.113.10:8080   ")).toEqual({
      server: "http://203.0.113.10:8080",
    });
  });

  it("rejects a disallowed scheme", () => {
    expect(parseProxyString("ftp://203.0.113.10:21")).toBeNull();
    expect(parseProxyString("ssh://203.0.113.10:22")).toBeNull();
  });

  it("rejects a missing or invalid port", () => {
    expect(parseProxyString("203.0.113.10")).toBeNull();
    expect(parseProxyString("203.0.113.10:")).toBeNull();
    expect(parseProxyString("203.0.113.10:notaport")).toBeNull();
    expect(parseProxyString("203.0.113.10:99999")).toBeNull();
    expect(parseProxyString("203.0.113.10:0")).toBeNull();
  });

  it("rejects garbage / partial input", () => {
    expect(parseProxyString("")).toBeNull();
    expect(parseProxyString("   ")).toBeNull();
    expect(parseProxyString("not a proxy at all")).toBeNull();
    expect(parseProxyString("host:port:only-three:fields:five")).toBeNull();
  });

  it("rejects an empty username in the 4-part colon form", () => {
    expect(parseProxyString("203.0.113.10:8080::mypass")).toBeNull();
  });

  it("rejects a scheme with no host:port after it", () => {
    expect(parseProxyString("http://")).toBeNull();
  });
});

describe("describeParsedProxy", () => {
  it("summarizes scheme, host:port, and auth presence -- never the password", () => {
    const summary = describeParsedProxy({
      server: "socks5://203.0.113.10:1080", username: "myuser", password: "secret",
    });
    expect(summary).toContain("socks5");
    expect(summary).toContain("203.0.113.10:1080");
    expect(summary).toContain("myuser");
    expect(summary).not.toContain("secret");
  });

  it("says 'no auth' when there is none", () => {
    expect(describeParsedProxy({ server: "http://203.0.113.10:8080" })).toContain("no auth");
  });
});
