# 🛍️ Smart Shopping Assistant

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Gradio](https://img.shields.io/badge/interface-Gradio-orange.svg)](https://gradio.app)
[![Redis](https://img.shields.io/badge/cache-Redis-red.svg)](https://redis.io)

An intelligent shopping assistant powered by Azure OpenAI, featuring advanced Named Entity Recognition (NER), semantic search, and personalized product recommendations for bags and accessories.

## 🌟 Key Features

### 🤖 **AI-Powered Conversation**
- **Azure OpenAI Integration**: Natural language understanding and response generation
- **Conversational Memory**: Maintains context across the entire shopping session
- **Smart Preference Extraction**: Automatically understands user preferences from natural language

### 🎯 **Advanced Product Search**
- **Semantic Vector Search**: ChromaDB-powered similarity search across 500+ products
- **Multi-criteria Filtering**: Price, brand, color, category, material, and feature filters
- **Exclusion Support**: "Show me bags but not black ones" or "Avoid Coach brand"
- **Hybrid Search Strategy**: Combines semantic search with database filtering for optimal results

### 🧠 **Named Entity Recognition (NER)**
- **Multiple Extraction Strategies**: spaCy NER, regex patterns, fuzzy matching, dictionary lookup
- **Entity Types**: Brands, colors, categories, prices, materials, features, exclusions
- **Confidence Scoring**: Reliability tracking for all extractions
- **Real-time Processing**: Instant entity extraction from user input

### ⚡ **Enterprise-Level Caching**
- **Redis Primary Cache**: High-performance caching with memory fallback
- **95% Performance Improvement**: Sub-second responses for cached queries
- **Multi-layer Caching**: Product data, search results, preference extractions
- **Smart TTL Management**: Different cache durations for different data types

### 👥 **Session Management**
- **Isolated User Sessions**: Prevents cross-contamination between users
- **Thread-Safe Operations**: Supports concurrent users
- **Automatic Cleanup**: 24-hour session timeout with background cleanup
- **Session-based Preferences**: Personalized experience per user

### 🎨 **Professional UI/UX**
- **Modern Gradio Interface**: Responsive web interface with custom styling
- **Real-time Updates**: Live preference display and product recommendations
- **Rich Product Cards**: High-quality images, detailed descriptions, and pricing
- **Mobile-Optimized**: Works seamlessly across all devices

## 🚀 Quick Start

### Prerequisites

- Python 3.8 or higher
- Redis server (optional but recommended for performance)
- Azure OpenAI API access

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/VenkatVeda/shopping-assistant.git
   cd shopping-assistant
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Download spaCy model**
   ```bash
   python -m spacy download en_core_web_sm
   ```

4. **Set up environment variables**
   ```bash
   # Copy example environment file
   cp .env.example .env
   
   # Edit .env with your configuration
   AZURE_OPENAI_API_KEY=your_api_key_here
   AZURE_OPENAI_ENDPOINT=your_endpoint_here
   AZURE_OPENAI_DEPLOYMENT_NAME=your_deployment_name
   AZURE_OPENAI_API_VERSION=2024-02-15-preview
   ```

5. **Start Redis (optional)**
   ```bash
   # Windows
   start_with_redis.bat
   
   # Linux/macOS
   ./start_with_redis.sh
   ```

6. **Launch the application**
   ```bash
   python main.py
   ```

## 🎮 Usage Examples

### Basic Search
```
User: "I'm looking for a blue tote bag under $100"
Assistant: Updates preferences and shows matching blue tote bags under $100
```

### Exclusion Filtering
```
User: "Show me handbags but not black or brown ones"
Assistant: Displays handbags excluding black and brown colors
```

### Brand-Specific Search
```
User: "I want Mimco bags, not interested in wallets"
Assistant: Shows Mimco bags excluding wallet categories
```

### Price Range Filtering
```
User: "Show me luxury bags between $200-500"
Assistant: Displays premium bags in the specified price range
```

## 📂 Project Structure

```
shopping-assistant/
├── main.py                    # Main application entry point
├── requirements.txt           # Python dependencies
├── docker-compose.yml         # Docker setup with Redis
├── assets/                    # Static assets and styling
├── config/                    # Configuration files
│   ├── settings.py           # Application settings
│   ├── prompts.py            # LLM prompts
│   └── ner_config.py         # NER configuration
├── data_layer/               # Data management
│   ├── embeddings.py         # Vector embeddings
│   └── bags.xlsx             # Product catalog
├── models/                   # Data models
│   ├── preferences.py        # User preferences model
│   ├── state.py              # Application state
│   └── enhanced_state.py     # NER state tracking
├── services/                 # Core business logic
│   ├── azure_service.py      # Azure OpenAI integration
│   ├── vector_service.py     # Vector database operations
│   ├── search_service.py     # Product search logic
│   ├── ner_service.py        # Named Entity Recognition
│   ├── enhanced_preference_service.py  # Advanced preference handling
│   └── session_manager.py    # User session management
├── ui/                       # User interface
│   ├── gradio_interface.py   # Web interface
│   └── formatters.py         # Product display formatting
├── utils/                    # Utility functions
│   ├── data_loader.py        # Data loading utilities
│   └── validators.py         # Data validation
├── workflows/                # Business workflows
│   └── conversation_flow.py  # Conversation management
├── tests/                    # Test suite (135 test cases)
│   ├── test_*.py             # Component tests
│   └── run_all_tests.py      # Test runner
└── docs/                     # Documentation
    ├── NER_IMPLEMENTATION_README.md
    ├── SESSION_MANAGEMENT_DOCUMENTATION.md
    └── TESTING_README.md
```

## 🧪 Testing

Run the comprehensive test suite (135 test cases, 92.6% pass rate):

```bash
# Run all tests
python tests/run_all_tests.py

# Run specific test categories
python tests/test_ner_functionality.py
python tests/test_conversational_flow.py
python tests/test_complete_pipeline.py

# Run conversational flow tests (13 personas, 91 scenarios)
python tests/run_conversational_tests.py
```

## 🐳 Docker Deployment

Deploy with Docker Compose (includes Redis):

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

## 🚀 Deployment Options

### Development Mode
```bash
python main.py dev
```

### Production Mode
```bash
python main.py prod
```

### Local Testing
```bash
python main.py local
```

### Service Testing
```bash
python main.py test
```

## ⚙️ Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key | Required |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL | Required |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | Deployment name | Required |
| `ENABLE_NER` | Enable/disable NER features | `true` |
| `ENABLE_REDIS` | Enable Redis caching | `true` |
| `MIN_CONFIDENCE_BRAND` | Minimum confidence for brand extraction | `0.7` |
| `MIN_CONFIDENCE_COLOR` | Minimum confidence for color extraction | `0.8` |

### Application Settings

Key settings in `config/settings.py`:
- **Session timeout**: 24 hours
- **Cache TTL**: Variable by data type (2-24 hours)
- **Max concurrent sessions**: 1000
- **Search result limit**: 30 documents

## 📊 Performance Metrics

- **Response Time**: <200ms for cached queries, <2s for new queries
- **Cache Hit Rate**: ~95% for repeated queries
- **Concurrent Users**: Tested up to 100 simultaneous sessions
- **Search Accuracy**: 92.6% test pass rate across 135 test cases
- **Product Catalog**: 500+ products with rich metadata

## 🛠️ API Reference

### Core Classes

#### `ShoppingAssistantApp`
Main application class with Redis caching integration.

#### `EnhancedPreferenceService`
Advanced preference management with NER integration.

#### `SessionManager`
Thread-safe session management for concurrent users.

#### `NERService`
Named Entity Recognition with multiple extraction strategies.

#### `SearchService`
Hybrid search combining semantic and database filtering.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Development Guidelines

- Follow PEP 8 style guidelines
- Add tests for new features
- Update documentation for API changes
- Ensure all tests pass before submitting PR

## 📋 Roadmap

### Immediate Priorities
- [ ] Authentication and authorization system
- [ ] Rate limiting and security enhancements
- [ ] Async/await conversion for better performance
- [ ] Enhanced error handling and monitoring

### Future Enhancements
- [ ] Multi-language support
- [ ] Machine learning recommendation engine
- [ ] User account system with purchase history
- [ ] Mobile app development
- [ ] Real-time inventory tracking

## 🔒 Security Considerations

- **API Keys**: Store in environment variables, never commit to repository
- **Session Security**: Implement session encryption for production
- **Rate Limiting**: Add request throttling to prevent abuse
- **Input Validation**: Sanitize all user inputs
- **HTTPS**: Use secure connections in production

## 📈 Monitoring

### Health Checks
```bash
# Check system status
curl http://localhost:7860/health

# View active sessions
curl http://localhost:7860/sessions/count
```

### Metrics
- Active sessions count
- Cache hit/miss ratios
- Response time distributions
- Error rates by component

## 🆘 Troubleshooting

### Common Issues

**Redis Connection Failed**
```bash
# Check Redis status
redis-cli ping

# Restart Redis service
sudo systemctl restart redis
```

**Azure OpenAI Timeout**
```bash
# Verify API credentials
python -c "from services.azure_service import AzureService; print(AzureService().is_available())"
```

**spaCy Model Missing**
```bash
# Download required model
python -m spacy download en_core_web_sm
```

**Memory Issues**
```bash
# Clear cache
redis-cli FLUSHALL

# Restart application
python main.py
```

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/VenkatVeda/shopping-assistant/issues)
- **Discussions**: [GitHub Discussions](https://github.com/VenkatVeda/shopping-assistant/discussions)
- **Documentation**: [docs/](./docs/) directory

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **Azure OpenAI** for advanced language model capabilities
- **Gradio** for the intuitive web interface framework
- **ChromaDB** for vector database functionality
- **spaCy** for Named Entity Recognition
- **Redis** for high-performance caching

---

<div align="center">

**Built with ❤️ using AI and modern Python technologies**

[⭐ Star this repository](https://github.com/VenkatVeda/shopping-assistant) if you find it helpful!

</div>