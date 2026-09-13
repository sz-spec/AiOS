"""
Template Library Service
=========================
Pre-built templates for quick project starts.

Categories:
- Landing Pages
- Dashboards
- E-commerce
- SaaS
- Portfolio
- Blog
- Admin
- Mobile Apps
"""

from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from enum import Enum

# =============================================================================
# Enums & Types
# =============================================================================


class TemplateCategory(str, Enum):
    """Template categories."""

    LANDING = "landing"
    DASHBOARD = "dashboard"
    ECOMMERCE = "ecommerce"
    SAAS = "saas"
    PORTFOLIO = "portfolio"
    BLOG = "blog"
    ADMIN = "admin"
    MOBILE = "mobile"
    MARKETING = "marketing"
    SOCIAL = "social"


class TemplateFramework(str, Enum):
    """Supported frameworks."""

    REACT = "react"
    NEXTJS = "nextjs"
    VUE = "vue"
    SVELTE = "svelte"
    HTML = "html"


class TemplateDifficulty(str, Enum):
    """Template complexity."""

    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


@dataclass
class TemplateFile:
    """File in a template."""

    path: str
    content: str
    language: str = "typescript"


@dataclass
class Template:
    """Template definition."""

    id: str
    name: str
    description: str
    category: TemplateCategory
    framework: TemplateFramework
    difficulty: TemplateDifficulty
    thumbnail: str
    preview_url: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    features: List[str] = field(default_factory=list)
    files: List[TemplateFile] = field(default_factory=list)
    dependencies: Dict[str, str] = field(default_factory=dict)
    is_premium: bool = False
    is_new: bool = False
    is_popular: bool = False
    downloads: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category.value
        data["framework"] = self.framework.value
        data["difficulty"] = self.difficulty.value
        data["created_at"] = self.created_at.isoformat()
        return data


# =============================================================================
# Template Definitions
# =============================================================================

TEMPLATES: List[Template] = [
    # -------------------------------------------------------------------------
    # Landing Pages
    # -------------------------------------------------------------------------
    Template(
        id="landing-saas",
        name="SaaS Landing Page",
        description="Modern SaaS landing page with hero, features, pricing, testimonials, and CTA sections.",
        category=TemplateCategory.LANDING,
        framework=TemplateFramework.NEXTJS,
        difficulty=TemplateDifficulty.BEGINNER,
        thumbnail="/templates/landing-saas.png",
        tags=["landing", "saas", "marketing", "responsive"],
        features=[
            "Hero section",
            "Feature grid",
            "Pricing table",
            "Testimonials",
            "FAQ accordion",
            "Newsletter signup",
        ],
        is_popular=True,
        downloads=12500,
        files=[
            TemplateFile(
                path="app/page.tsx",
                content="""import { Hero } from '@/components/Hero';
import { Features } from '@/components/Features';
import { Pricing } from '@/components/Pricing';
import { Testimonials } from '@/components/Testimonials';
import { FAQ } from '@/components/FAQ';
import { CTA } from '@/components/CTA';

export default function LandingPage() {
  return (
    <main className="min-h-screen">
      <Hero 
        title="Build faster with AI"
        subtitle="The most powerful AI-powered development platform"
        ctaText="Start Free Trial"
        ctaLink="/signup"
      />
      <Features />
      <Pricing />
      <Testimonials />
      <FAQ />
      <CTA />
    </main>
  );
}""",
                language="typescript",
            ),
            TemplateFile(
                path="components/Hero.tsx",
                content="""export function Hero({ title, subtitle, ctaText, ctaLink }) {
  return (
    <section className="relative py-20 px-4 bg-gradient-to-br from-purple-900 via-purple-800 to-indigo-900">
      <div className="max-w-6xl mx-auto text-center">
        <h1 className="text-5xl md:text-7xl font-bold text-white mb-6">
          {title}
        </h1>
        <p className="text-xl text-purple-200 mb-8 max-w-2xl mx-auto">
          {subtitle}
        </p>
        <div className="flex gap-4 justify-center">
          <a href={ctaLink} className="px-8 py-4 bg-white text-purple-900 rounded-lg font-semibold hover:bg-purple-100 transition">
            {ctaText}
          </a>
          <a href="#demo" className="px-8 py-4 border border-white text-white rounded-lg font-semibold hover:bg-white/10 transition">
            Watch Demo
          </a>
        </div>
      </div>
    </section>
  );
}""",
                language="typescript",
            ),
            TemplateFile(
                path="components/Features.tsx",
                content="""const features = [
  { icon: "⚡", title: "Lightning Fast", description: "Generate code in seconds, not hours" },
  { icon: "🎨", title: "Beautiful UI", description: "Production-ready components out of the box" },
  { icon: "🔒", title: "Secure", description: "Enterprise-grade security built in" },
  { icon: "🚀", title: "Scalable", description: "From MVP to millions of users" },
];

export function Features() {
  return (
    <section className="py-20 px-4 bg-white">
      <div className="max-w-6xl mx-auto">
        <h2 className="text-4xl font-bold text-center mb-12">Why choose us?</h2>
        <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-8">
          {features.map((feature, i) => (
            <div key={i} className="text-center p-6 rounded-xl bg-gray-50 hover:shadow-lg transition">
              <div className="text-4xl mb-4">{feature.icon}</div>
              <h3 className="text-xl font-semibold mb-2">{feature.title}</h3>
              <p className="text-gray-600">{feature.description}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}""",
                language="typescript",
            ),
            TemplateFile(
                path="components/Pricing.tsx",
                content="""const plans = [
  { name: "Free", price: 0, features: ["3 projects", "Basic templates", "Community support"] },
  { name: "Pro", price: 29, features: ["Unlimited projects", "All templates", "Priority support", "GitHub sync"], popular: true },
  { name: "Team", price: 99, features: ["Everything in Pro", "5 team members", "API access", "SLA support"] },
];

export function Pricing() {
  return (
    <section className="py-20 px-4 bg-gray-50">
      <div className="max-w-6xl mx-auto">
        <h2 className="text-4xl font-bold text-center mb-12">Simple Pricing</h2>
        <div className="grid md:grid-cols-3 gap-8">
          {plans.map((plan, i) => (
            <div key={i} className={`p-8 rounded-2xl ${plan.popular ? 'bg-purple-900 text-white ring-4 ring-purple-500' : 'bg-white'}`}>
              {plan.popular && <span className="text-sm bg-purple-500 px-3 py-1 rounded-full">Most Popular</span>}
              <h3 className="text-2xl font-bold mt-4">{plan.name}</h3>
              <div className="text-4xl font-bold my-4">${plan.price}<span className="text-lg">/mo</span></div>
              <ul className="space-y-3 mb-8">
                {plan.features.map((f, j) => <li key={j}>✓ {f}</li>)}
              </ul>
              <button className={`w-full py-3 rounded-lg font-semibold ${plan.popular ? 'bg-white text-purple-900' : 'bg-purple-600 text-white'}`}>
                Get Started
              </button>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}""",
                language="typescript",
            ),
        ],
        dependencies={"framer-motion": "^10.0.0", "lucide-react": "^0.263.1"},
    ),
    # -------------------------------------------------------------------------
    # Dashboards
    # -------------------------------------------------------------------------
    Template(
        id="dashboard-analytics",
        name="Analytics Dashboard",
        description="Data-rich analytics dashboard with charts, metrics, and real-time updates.",
        category=TemplateCategory.DASHBOARD,
        framework=TemplateFramework.NEXTJS,
        difficulty=TemplateDifficulty.INTERMEDIATE,
        thumbnail="/templates/dashboard-analytics.png",
        tags=["dashboard", "analytics", "charts", "data"],
        features=[
            "KPI cards",
            "Line/Bar/Pie charts",
            "Data tables",
            "Date range picker",
            "Export to CSV",
            "Real-time updates",
        ],
        is_popular=True,
        downloads=9800,
        files=[
            TemplateFile(
                path="app/dashboard/page.tsx",
                content="""import { KPICards } from '@/components/dashboard/KPICards';
import { RevenueChart } from '@/components/dashboard/RevenueChart';
import { UsersChart } from '@/components/dashboard/UsersChart';
import { RecentActivity } from '@/components/dashboard/RecentActivity';
import { TopProducts } from '@/components/dashboard/TopProducts';

export default function DashboardPage() {
  return (
    <div className="p-6 bg-gray-100 min-h-screen">
      <div className="flex justify-between items-center mb-8">
        <h1 className="text-3xl font-bold">Dashboard</h1>
        <select className="px-4 py-2 border rounded-lg">
          <option>Last 7 days</option>
          <option>Last 30 days</option>
          <option>Last 90 days</option>
        </select>
      </div>
      
      <KPICards />
      
      <div className="grid lg:grid-cols-2 gap-6 mt-6">
        <RevenueChart />
        <UsersChart />
      </div>
      
      <div className="grid lg:grid-cols-3 gap-6 mt-6">
        <div className="lg:col-span-2">
          <RecentActivity />
        </div>
        <TopProducts />
      </div>
    </div>
  );
}""",
                language="typescript",
            ),
            TemplateFile(
                path="components/dashboard/KPICards.tsx",
                content="""const kpis = [
  { label: "Total Revenue", value: "$45,231", change: "+12.5%", positive: true },
  { label: "Active Users", value: "2,345", change: "+8.2%", positive: true },
  { label: "Conversion Rate", value: "3.24%", change: "-0.4%", positive: false },
  { label: "Avg. Order Value", value: "$124", change: "+5.1%", positive: true },
];

export function KPICards() {
  return (
    <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-6">
      {kpis.map((kpi, i) => (
        <div key={i} className="bg-white p-6 rounded-xl shadow-sm">
          <p className="text-gray-500 text-sm">{kpi.label}</p>
          <p className="text-3xl font-bold mt-2">{kpi.value}</p>
          <p className={`text-sm mt-2 ${kpi.positive ? 'text-green-600' : 'text-red-600'}`}>
            {kpi.change} vs last period
          </p>
        </div>
      ))}
    </div>
  );
}""",
                language="typescript",
            ),
            TemplateFile(
                path="components/dashboard/RevenueChart.tsx",
                content="""import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';

const data = [
  { name: 'Mon', revenue: 4000 },
  { name: 'Tue', revenue: 3000 },
  { name: 'Wed', revenue: 5000 },
  { name: 'Thu', revenue: 2780 },
  { name: 'Fri', revenue: 1890 },
  { name: 'Sat', revenue: 2390 },
  { name: 'Sun', revenue: 3490 },
];

export function RevenueChart() {
  return (
    <div className="bg-white p-6 rounded-xl shadow-sm">
      <h3 className="text-lg font-semibold mb-4">Revenue Overview</h3>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="name" />
          <YAxis />
          <Tooltip />
          <Line type="monotone" dataKey="revenue" stroke="#8b5cf6" strokeWidth={2} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}""",
                language="typescript",
            ),
        ],
        dependencies={"recharts": "^2.10.0", "@tanstack/react-table": "^8.0.0"},
    ),
    # -------------------------------------------------------------------------
    # E-commerce
    # -------------------------------------------------------------------------
    Template(
        id="ecommerce-store",
        name="E-commerce Store",
        description="Full-featured online store with product catalog, cart, and checkout.",
        category=TemplateCategory.ECOMMERCE,
        framework=TemplateFramework.NEXTJS,
        difficulty=TemplateDifficulty.ADVANCED,
        thumbnail="/templates/ecommerce-store.png",
        tags=["ecommerce", "shop", "cart", "payments"],
        features=[
            "Product catalog",
            "Search & filters",
            "Shopping cart",
            "Checkout flow",
            "Order tracking",
            "Wishlist",
        ],
        is_premium=True,
        downloads=7200,
        files=[
            TemplateFile(
                path="app/shop/page.tsx",
                content="""import { ProductGrid } from '@/components/shop/ProductGrid';
import { Filters } from '@/components/shop/Filters';
import { SearchBar } from '@/components/shop/SearchBar';

export default function ShopPage() {
  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-7xl mx-auto px-4 py-8">
        <div className="flex justify-between items-center mb-8">
          <h1 className="text-3xl font-bold">Shop</h1>
          <SearchBar />
        </div>
        
        <div className="flex gap-8">
          <aside className="w-64 flex-shrink-0">
            <Filters />
          </aside>
          
          <main className="flex-1">
            <ProductGrid />
          </main>
        </div>
      </div>
    </div>
  );
}""",
                language="typescript",
            ),
            TemplateFile(
                path="components/shop/ProductCard.tsx",
                content="""import Image from 'next/image';
import { useCart } from '@/hooks/useCart';

export function ProductCard({ product }) {
  const { addItem } = useCart();
  
  return (
    <div className="bg-white rounded-xl overflow-hidden shadow-sm hover:shadow-lg transition group">
      <div className="relative aspect-square">
        <Image src={product.image} alt={product.name} fill className="object-cover group-hover:scale-105 transition" />
        {product.sale && (
          <span className="absolute top-2 left-2 bg-red-500 text-white px-2 py-1 text-sm rounded">Sale</span>
        )}
        <button className="absolute top-2 right-2 p-2 bg-white rounded-full opacity-0 group-hover:opacity-100 transition">
          ❤️
        </button>
      </div>
      
      <div className="p-4">
        <p className="text-sm text-gray-500">{product.category}</p>
        <h3 className="font-semibold mt-1">{product.name}</h3>
        <div className="flex items-center gap-2 mt-2">
          <span className="text-lg font-bold">${product.price}</span>
          {product.originalPrice && (
            <span className="text-gray-400 line-through">${product.originalPrice}</span>
          )}
        </div>
        <button 
          onClick={() => addItem(product)}
          className="w-full mt-4 py-2 bg-black text-white rounded-lg hover:bg-gray-800 transition"
        >
          Add to Cart
        </button>
      </div>
    </div>
  );
}""",
                language="typescript",
            ),
            TemplateFile(
                path="hooks/useCart.ts",
                content="""import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface CartItem {
  id: string;
  name: string;
  price: number;
  quantity: number;
  image: string;
}

interface CartStore {
  items: CartItem[];
  addItem: (product: any) => void;
  removeItem: (id: string) => void;
  updateQuantity: (id: string, quantity: number) => void;
  clearCart: () => void;
  total: () => number;
}

export const useCart = create<CartStore>()(
  persist(
    (set, get) => ({
      items: [],
      addItem: (product) => {
        set((state) => {
          const existing = state.items.find(i => i.id === product.id);
          if (existing) {
            return { items: state.items.map(i => i.id === product.id ? { ...i, quantity: i.quantity + 1 } : i) };
          }
          return { items: [...state.items, { ...product, quantity: 1 }] };
        });
      },
      removeItem: (id) => set((state) => ({ items: state.items.filter(i => i.id !== id) })),
      updateQuantity: (id, quantity) => set((state) => ({
        items: state.items.map(i => i.id === id ? { ...i, quantity } : i)
      })),
      clearCart: () => set({ items: [] }),
      total: () => get().items.reduce((sum, i) => sum + i.price * i.quantity, 0),
    }),
    { name: 'cart-storage' }
  )
);""",
                language="typescript",
            ),
        ],
        dependencies={"zustand": "^4.4.0", "@stripe/stripe-js": "^2.0.0"},
    ),
    # -------------------------------------------------------------------------
    # Portfolio
    # -------------------------------------------------------------------------
    Template(
        id="portfolio-developer",
        name="Developer Portfolio",
        description="Clean, minimal portfolio for developers showcasing projects and skills.",
        category=TemplateCategory.PORTFOLIO,
        framework=TemplateFramework.NEXTJS,
        difficulty=TemplateDifficulty.BEGINNER,
        thumbnail="/templates/portfolio-developer.png",
        tags=["portfolio", "developer", "minimal", "personal"],
        features=[
            "About section",
            "Project showcase",
            "Skills grid",
            "Contact form",
            "Blog integration",
            "Dark mode",
        ],
        is_new=True,
        downloads=5400,
        files=[
            TemplateFile(
                path="app/page.tsx",
                content="""import { Hero } from '@/components/Hero';
import { About } from '@/components/About';
import { Projects } from '@/components/Projects';
import { Skills } from '@/components/Skills';
import { Contact } from '@/components/Contact';

export default function Portfolio() {
  return (
    <main className="min-h-screen bg-[#0a0a0b] text-white">
      <Hero />
      <About />
      <Projects />
      <Skills />
      <Contact />
    </main>
  );
}""",
                language="typescript",
            ),
            TemplateFile(
                path="components/Hero.tsx",
                content="""export function Hero() {
  return (
    <section className="min-h-screen flex items-center justify-center px-4">
      <div className="text-center">
        <p className="text-purple-400 mb-4">Hi, my name is</p>
        <h1 className="text-6xl md:text-8xl font-bold mb-4">John Doe</h1>
        <h2 className="text-3xl md:text-5xl text-gray-400 mb-8">I build things for the web.</h2>
        <p className="text-gray-400 max-w-xl mx-auto mb-12">
          I'm a full-stack developer specializing in building exceptional digital experiences.
        </p>
        <div className="flex gap-4 justify-center">
          <a href="#projects" className="px-8 py-4 border border-purple-500 text-purple-400 rounded hover:bg-purple-500/10 transition">
            View My Work
          </a>
          <a href="#contact" className="px-8 py-4 bg-purple-600 text-white rounded hover:bg-purple-700 transition">
            Get In Touch
          </a>
        </div>
      </div>
    </section>
  );
}""",
                language="typescript",
            ),
            TemplateFile(
                path="components/Projects.tsx",
                content="""const projects = [
  {
    title: "E-commerce Platform",
    description: "Full-stack e-commerce solution with React, Node.js, and Stripe integration.",
    image: "/projects/ecommerce.png",
    tags: ["React", "Node.js", "PostgreSQL", "Stripe"],
    github: "https://github.com",
    live: "https://example.com"
  },
  {
    title: "AI Chat Application",
    description: "Real-time chat app powered by GPT-4 with conversation memory.",
    image: "/projects/ai-chat.png",
    tags: ["Next.js", "OpenAI", "WebSocket", "Redis"],
    github: "https://github.com",
    live: "https://example.com"
  },
];

export function Projects() {
  return (
    <section id="projects" className="py-20 px-4">
      <div className="max-w-6xl mx-auto">
        <h2 className="text-4xl font-bold mb-12">Featured Projects</h2>
        <div className="space-y-20">
          {projects.map((project, i) => (
            <div key={i} className={`flex flex-col ${i % 2 ? 'md:flex-row-reverse' : 'md:flex-row'} gap-8 items-center`}>
              <div className="flex-1">
                <img src={project.image} alt={project.title} className="rounded-lg shadow-2xl" />
              </div>
              <div className="flex-1">
                <h3 className="text-2xl font-bold mb-4">{project.title}</h3>
                <p className="text-gray-400 mb-4">{project.description}</p>
                <div className="flex flex-wrap gap-2 mb-6">
                  {project.tags.map((tag, j) => (
                    <span key={j} className="px-3 py-1 bg-purple-500/20 text-purple-400 rounded text-sm">{tag}</span>
                  ))}
                </div>
                <div className="flex gap-4">
                  <a href={project.github} className="text-gray-400 hover:text-white">GitHub →</a>
                  <a href={project.live} className="text-gray-400 hover:text-white">Live Demo →</a>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}""",
                language="typescript",
            ),
        ],
        dependencies={"framer-motion": "^10.0.0"},
    ),
    # -------------------------------------------------------------------------
    # Blog
    # -------------------------------------------------------------------------
    Template(
        id="blog-minimal",
        name="Minimal Blog",
        description="Clean, typography-focused blog with MDX support and dark mode.",
        category=TemplateCategory.BLOG,
        framework=TemplateFramework.NEXTJS,
        difficulty=TemplateDifficulty.INTERMEDIATE,
        thumbnail="/templates/blog-minimal.png",
        tags=["blog", "mdx", "minimal", "writing"],
        features=[
            "MDX support",
            "Syntax highlighting",
            "Table of contents",
            "Reading time",
            "Newsletter",
            "RSS feed",
        ],
        downloads=4200,
        files=[
            TemplateFile(
                path="app/blog/page.tsx",
                content="""import { getAllPosts } from '@/lib/posts';
import { PostCard } from '@/components/blog/PostCard';

export default async function BlogPage() {
  const posts = await getAllPosts();
  
  return (
    <div className="max-w-3xl mx-auto px-4 py-16">
      <h1 className="text-4xl font-bold mb-4">Blog</h1>
      <p className="text-gray-600 dark:text-gray-400 mb-12">
        Thoughts on development, design, and building products.
      </p>
      
      <div className="space-y-12">
        {posts.map((post) => (
          <PostCard key={post.slug} post={post} />
        ))}
      </div>
    </div>
  );
}""",
                language="typescript",
            ),
            TemplateFile(
                path="components/blog/PostCard.tsx",
                content="""import Link from 'next/link';

export function PostCard({ post }) {
  return (
    <article className="group">
      <Link href={`/blog/${post.slug}`}>
        <time className="text-sm text-gray-500">{post.date}</time>
        <h2 className="text-2xl font-semibold mt-2 group-hover:text-purple-600 transition">
          {post.title}
        </h2>
        <p className="text-gray-600 dark:text-gray-400 mt-3 line-clamp-2">
          {post.excerpt}
        </p>
        <div className="flex items-center gap-4 mt-4 text-sm text-gray-500">
          <span>{post.readingTime} min read</span>
          <span>•</span>
          <div className="flex gap-2">
            {post.tags.map((tag) => (
              <span key={tag} className="text-purple-600">#{tag}</span>
            ))}
          </div>
        </div>
      </Link>
    </article>
  );
}""",
                language="typescript",
            ),
        ],
        dependencies={
            "next-mdx-remote": "^4.4.0",
            "rehype-highlight": "^7.0.0",
            "reading-time": "^1.5.0",
        },
    ),
    # -------------------------------------------------------------------------
    # SaaS
    # -------------------------------------------------------------------------
    Template(
        id="saas-starter",
        name="SaaS Starter Kit",
        description="Complete SaaS boilerplate with auth, billing, dashboard, and settings.",
        category=TemplateCategory.SAAS,
        framework=TemplateFramework.NEXTJS,
        difficulty=TemplateDifficulty.ADVANCED,
        thumbnail="/templates/saas-starter.png",
        tags=["saas", "auth", "billing", "starter"],
        features=[
            "Auth (OAuth + Email)",
            "Stripe billing",
            "User dashboard",
            "Team management",
            "API keys",
            "Usage tracking",
        ],
        is_premium=True,
        is_popular=True,
        downloads=15000,
        files=[
            TemplateFile(
                path="app/(dashboard)/layout.tsx",
                content="""import { Sidebar } from '@/components/dashboard/Sidebar';
import { Header } from '@/components/dashboard/Header';
import { getSession } from '@/lib/auth';
import { redirect } from 'next/navigation';

export default async function DashboardLayout({ children }) {
  const session = await getSession();
  if (!session) redirect('/login');
  
  return (
    <div className="flex h-screen bg-gray-100 dark:bg-gray-900">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header user={session.user} />
        <main className="flex-1 overflow-y-auto p-6">
          {children}
        </main>
      </div>
    </div>
  );
}""",
                language="typescript",
            ),
            TemplateFile(
                path="components/dashboard/Sidebar.tsx",
                content="""import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Home, Settings, CreditCard, Users, Key, BarChart } from 'lucide-react';

const navigation = [
  { name: 'Dashboard', href: '/dashboard', icon: Home },
  { name: 'Analytics', href: '/dashboard/analytics', icon: BarChart },
  { name: 'Team', href: '/dashboard/team', icon: Users },
  { name: 'API Keys', href: '/dashboard/api-keys', icon: Key },
  { name: 'Billing', href: '/dashboard/billing', icon: CreditCard },
  { name: 'Settings', href: '/dashboard/settings', icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();
  
  return (
    <aside className="w-64 bg-white dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700">
      <div className="p-6">
        <h1 className="text-2xl font-bold">🚀 SaaSKit</h1>
      </div>
      
      <nav className="px-4 space-y-1">
        {navigation.map((item) => {
          const isActive = pathname === item.href;
          return (
            <Link
              key={item.name}
              href={item.href}
              className={`flex items-center gap-3 px-4 py-3 rounded-lg transition ${
                isActive 
                  ? 'bg-purple-100 text-purple-900 dark:bg-purple-900/50 dark:text-purple-100' 
                  : 'text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700'
              }`}
            >
              <item.icon className="w-5 h-5" />
              {item.name}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}""",
                language="typescript",
            ),
        ],
        dependencies={
            "stripe": "^14.0.0",
            "@stripe/stripe-js": "^2.0.0",
            "lucide-react": "^0.263.1",
        },
    ),
    # -------------------------------------------------------------------------
    # Admin Panel
    # -------------------------------------------------------------------------
    Template(
        id="admin-panel",
        name="Admin Panel",
        description="Comprehensive admin dashboard with user management, CRUD operations, and analytics.",
        category=TemplateCategory.ADMIN,
        framework=TemplateFramework.NEXTJS,
        difficulty=TemplateDifficulty.ADVANCED,
        thumbnail="/templates/admin-panel.png",
        tags=["admin", "crud", "tables", "management"],
        features=[
            "User management",
            "Data tables",
            "CRUD operations",
            "Role permissions",
            "Audit logs",
            "Export data",
        ],
        is_premium=True,
        downloads=6300,
        files=[
            TemplateFile(
                path="app/admin/users/page.tsx",
                content="""import { DataTable } from '@/components/admin/DataTable';
import { userColumns } from '@/components/admin/columns/users';
import { getUsers } from '@/lib/admin';

export default async function UsersPage() {
  const users = await getUsers();
  
  return (
    <div>
      <div className="flex justify-between items-center mb-8">
        <div>
          <h1 className="text-3xl font-bold">Users</h1>
          <p className="text-gray-500">Manage your platform users</p>
        </div>
        <button className="px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700">
          + Add User
        </button>
      </div>
      
      <DataTable columns={userColumns} data={users} searchKey="email" />
    </div>
  );
}""",
                language="typescript",
            ),
        ],
        dependencies={"@tanstack/react-table": "^8.0.0", "lucide-react": "^0.263.1"},
    ),
    # -------------------------------------------------------------------------
    # Mobile / PWA
    # -------------------------------------------------------------------------
    Template(
        id="mobile-pwa",
        name="Mobile PWA App",
        description="Progressive Web App with native-like experience, offline support, and push notifications.",
        category=TemplateCategory.MOBILE,
        framework=TemplateFramework.NEXTJS,
        difficulty=TemplateDifficulty.INTERMEDIATE,
        thumbnail="/templates/mobile-pwa.png",
        tags=["pwa", "mobile", "offline", "responsive"],
        features=[
            "Offline support",
            "Push notifications",
            "Install prompt",
            "Bottom navigation",
            "Pull to refresh",
            "Splash screen",
        ],
        is_new=True,
        downloads=3100,
        files=[
            TemplateFile(
                path="app/layout.tsx",
                content="""import { BottomNav } from '@/components/mobile/BottomNav';
import { PWAPrompt } from '@/components/mobile/PWAPrompt';

export default function MobileLayout({ children }) {
  return (
    <div className="min-h-screen bg-gray-50 pb-20">
      <PWAPrompt />
      <main>{children}</main>
      <BottomNav />
    </div>
  );
}""",
                language="typescript",
            ),
            TemplateFile(
                path="components/mobile/BottomNav.tsx",
                content="""import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Home, Search, Bell, User } from 'lucide-react';

const tabs = [
  { href: '/', icon: Home, label: 'Home' },
  { href: '/search', icon: Search, label: 'Search' },
  { href: '/notifications', icon: Bell, label: 'Alerts' },
  { href: '/profile', icon: User, label: 'Profile' },
];

export function BottomNav() {
  const pathname = usePathname();
  
  return (
    <nav className="fixed bottom-0 left-0 right-0 bg-white border-t border-gray-200 safe-area-pb">
      <div className="flex justify-around py-2">
        {tabs.map((tab) => {
          const isActive = pathname === tab.href;
          return (
            <Link
              key={tab.href}
              href={tab.href}
              className={`flex flex-col items-center py-2 px-4 ${isActive ? 'text-purple-600' : 'text-gray-500'}`}
            >
              <tab.icon className="w-6 h-6" />
              <span className="text-xs mt-1">{tab.label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}""",
                language="typescript",
            ),
        ],
        dependencies={"next-pwa": "^5.6.0", "lucide-react": "^0.263.1"},
    ),
]


# =============================================================================
# Template Service
# =============================================================================


class TemplateService:
    """
    Template library service.

    Usage:
        service = TemplateService()

        # Get all templates
        templates = service.get_all()

        # Filter by category
        landing_templates = service.get_by_category(TemplateCategory.LANDING)

        # Get template with files
        template = service.get_by_id("landing-saas")
    """

    def __init__(self):
        self._templates = {t.id: t for t in TEMPLATES}

    def get_all(
        self,
        include_premium: bool = True,
        framework: Optional[TemplateFramework] = None,
    ) -> List[Template]:
        """Get all templates."""
        templates = list(self._templates.values())

        if not include_premium:
            templates = [t for t in templates if not t.is_premium]

        if framework:
            templates = [t for t in templates if t.framework == framework]

        return sorted(templates, key=lambda t: t.downloads, reverse=True)

    def get_by_id(self, template_id: str) -> Optional[Template]:
        """Get template by ID."""
        return self._templates.get(template_id)

    def get_by_category(self, category: TemplateCategory) -> List[Template]:
        """Get templates by category."""
        return [t for t in self._templates.values() if t.category == category]

    def get_popular(self, limit: int = 6) -> List[Template]:
        """Get most popular templates."""
        return sorted(
            [t for t in self._templates.values() if t.is_popular],
            key=lambda t: t.downloads,
            reverse=True,
        )[:limit]

    def get_new(self, limit: int = 6) -> List[Template]:
        """Get newest templates."""
        return [t for t in self._templates.values() if t.is_new][:limit]

    def search(self, query: str) -> List[Template]:
        """Search templates by name, description, or tags."""
        query = query.lower()
        results = []

        for template in self._templates.values():
            score = 0
            if query in template.name.lower():
                score += 10
            if query in template.description.lower():
                score += 5
            if any(query in tag for tag in template.tags):
                score += 3

            if score > 0:
                results.append((score, template))

        return [t for _, t in sorted(results, key=lambda x: x[0], reverse=True)]

    def get_categories(self) -> List[Dict[str, Any]]:
        """Get all categories with counts."""
        categories = {}
        for template in self._templates.values():
            cat = template.category.value
            if cat not in categories:
                categories[cat] = {
                    "name": cat,
                    "count": 0,
                    "icon": self._get_category_icon(cat),
                }
            categories[cat]["count"] += 1

        return list(categories.values())

    def _get_category_icon(self, category: str) -> str:
        """Get icon for category."""
        icons = {
            "landing": "🚀",
            "dashboard": "📊",
            "ecommerce": "🛒",
            "saas": "💼",
            "portfolio": "👤",
            "blog": "📝",
            "admin": "⚙️",
            "mobile": "📱",
            "marketing": "📣",
            "social": "💬",
        }
        return icons.get(category, "📦")

    async def increment_downloads(self, template_id: str) -> bool:
        """Increment download count."""
        if template_id in self._templates:
            self._templates[template_id].downloads += 1
            return True
        return False


# =============================================================================
# Singleton
# =============================================================================

_template_service: Optional[TemplateService] = None


def get_template_service() -> TemplateService:
    """Get template service singleton."""
    global _template_service
    if _template_service is None:
        _template_service = TemplateService()
    return _template_service
